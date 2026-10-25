"""Config flow of the Fröling Lambdatronic integration."""

import logging
from collections.abc import Mapping
from typing import Any

import probatio
from froeling_lambdatronic import (
    AuthenticationError,
    CommunicationError,
    Equipment,
    Facility,
    FroelingConnectClient,
    FroelingError,
    LambdatronicClient,
    NoResponseError,
)
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_BUFFERS,
    CONF_CLOUD,
    CONF_EQUIPMENT,
    CONF_FACILITY_ID,
    CONF_HEATING_CIRCUITS,
    CONF_HOT_WATER,
    CONF_PARAMETER_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_SLAVE,
    DEFAULT_NAME,
    DEFAULT_PARAMETER_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SLAVE,
    DOMAIN,
    MAX_PARAMETER_INTERVAL,
    MAX_SCAN_INTERVAL,
    MIN_PARAMETER_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .coordinator import FroelingConfigEntry

_LOGGER = logging.getLogger(__name__)

EQUIPMENT_KEYS = (CONF_HEATING_CIRCUITS, CONF_HOT_WATER, CONF_BUFFERS)


def _box(minimum: int, maximum: int, unit: str | None = None) -> probatio.All[int]:
    config = NumberSelectorConfig(min=minimum, max=maximum, step=1, mode=NumberSelectorMode.BOX)
    if unit is not None:
        config["unit_of_measurement"] = unit
    return probatio.All(NumberSelector(config), probatio.Coerce(int))


CONNECTION_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_HOST): TextSelector(),
        probatio.Required(CONF_PORT, default=DEFAULT_PORT): _box(1, 65535),
        probatio.Required(CONF_SLAVE, default=DEFAULT_SLAVE): _box(1, 247),
    }
)
USER_SCHEMA = CONNECTION_SCHEMA.extend({probatio.Required(CONF_NAME, default=DEFAULT_NAME): TextSelector()})
CLOUD_SCHEMA = probatio.Schema(
    {
        probatio.Optional(CONF_USERNAME): TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL)),
        probatio.Optional(CONF_PASSWORD): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
    }
)
REAUTH_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_USERNAME): TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL)),
        probatio.Required(CONF_PASSWORD): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
    }
)


def _equipment_data(equipment: Equipment) -> dict[str, list[int]]:
    return {
        CONF_HEATING_CIRCUITS: sorted(equipment.heating_circuits),
        CONF_HOT_WATER: sorted(equipment.hot_water),
        CONF_BUFFERS: sorted(equipment.buffers),
    }


def _equipment_schema(
    detected: Mapping[str, list[int]], selected: Mapping[str, list[int]]
) -> probatio.Schema:
    """Multi-selects of the detected components, preselected with the current selection."""
    fields: dict[probatio.Marker, Any] = {}
    for key in EQUIPMENT_KEYS:
        if not detected[key]:
            continue
        options = [SelectOptionDict(value=str(n), label=str(n)) for n in detected[key]]
        # Only offered numbers can be preselected, stale ones would fail the validation.
        default = [str(n) for n in selected.get(key, detected[key]) if n in detected[key]]
        fields[probatio.Required(key, default=default)] = SelectSelector(
            SelectSelectorConfig(options=options, multiple=True, mode=SelectSelectorMode.LIST)
        )
    return probatio.Schema(fields)


def _reconfigured_equipment(
    entry: FroelingConfigEntry, detected: Mapping[str, list[int]], same_boiler: bool
) -> tuple[dict[str, list[int]], dict[str, Any]]:
    """Stored equipment and options after a reconfiguration.

    For the same boiler the stored equipment is the union of the previous and the new
    detection, so a component missed once keeps its device. Newly detected components
    are enabled in the options.
    """
    previous = entry.data[CONF_EQUIPMENT]
    equipment: dict[str, list[int]] = {}
    options: dict[str, Any] = dict(entry.options)
    for key in EQUIPMENT_KEYS:
        stored = set(detected[key]) | set(previous[key]) if same_boiler else set(detected[key])
        added = set(detected[key]) - set(previous[key])
        enabled = set(entry.options.get(key, previous[key])) | added
        equipment[key] = sorted(stored)
        options[key] = sorted(enabled & stored)
    return equipment, options


def _equipment_selection(
    detected: Mapping[str, list[int]], user_input: Mapping[str, Any]
) -> dict[str, list[int]]:
    return {
        key: sorted(int(n) for n in user_input.get(key, [])) if detected[key] else []
        for key in EQUIPMENT_KEYS
    }


async def _async_detect(
    host: str, port: int, slave: int, running: LambdatronicClient | None = None
) -> Equipment:
    """Read the system state and detect the equipment.

    A running client of the same entry is reused: the gateway mixes up answers
    when two clients talk to it at the same time.
    """
    if running is not None:
        await running.probe()
        return await running.detect_equipment()
    client = LambdatronicClient(host, port, slave)
    try:
        await client.connect()
        await client.probe()
        return await client.detect_equipment()
    finally:
        await client.close()


def _modbus_error(err: FroelingError) -> str:
    if isinstance(err, NoResponseError):
        return "no_response"
    if isinstance(err, CommunicationError):
        return "invalid_slave"
    return "cannot_connect"


async def _async_check_cloud(
    hass: HomeAssistant, username: str, password: str, facility_id: int | None = None
) -> tuple[str | None, list[Facility]]:
    """Log in to Fröling Connect.

    Returns an error key or the facilities still to choose from. A single facility
    in the result is the chosen one; its state could be read. Remote control being
    disabled does not block the flow: the cloud coordinator raises a repair issue.
    """
    client = FroelingConnectClient(async_get_clientsession(hass), username, password)
    try:
        await client.login()
        facilities = await client.get_facilities()
        if facility_id is not None:
            facilities = [f for f in facilities if f.facility_id == facility_id]
        if not facilities:
            return "no_facilities", []
        if len(facilities) > 1:
            return None, facilities
        await client.get_boiler_state(facilities[0].facility_id)
    except AuthenticationError:
        return "invalid_auth", []
    except FroelingError:
        return "cloud_cannot_connect", []
    except Exception:
        _LOGGER.exception("Unexpected error while logging in to Fröling Connect")
        return "unknown", []
    return None, facilities


class FroelingConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a Lambdatronic boiler: connection, equipment, optional Fröling Connect."""

    VERSION = 1

    def __init__(self) -> None:
        self._name = DEFAULT_NAME
        self._data: dict[str, Any] = {}
        self._options: dict[str, Any] = {}
        self._credentials: dict[str, str] = {}
        self._facilities: list[Facility] = []

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: FroelingConfigEntry) -> FroelingOptionsFlow:
        """Options: equipment and polling intervals."""
        return FroelingOptionsFlow()

    async def _async_connection_step(
        self, step_id: str, schema: probatio.Schema, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult | None:
        """Validate host, port and slave; returns a form on error, None on success."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_PORT]
            slave = user_input[CONF_SLAVE]
            unique_id = f"{host}:{port}:{slave}"
            await self.async_set_unique_id(unique_id)
            if self.source != SOURCE_RECONFIGURE or unique_id != self._get_reconfigure_entry().unique_id:
                self._abort_if_unique_id_configured()
            try:
                equipment = await _async_detect(host, port, slave, self._running_client(host, port, slave))
            except FroelingError as err:
                errors["base"] = _modbus_error(err)
            except Exception:
                _LOGGER.exception("Unexpected error while connecting to %s", host)
                errors["base"] = "unknown"
            else:
                self._data.update(
                    {
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_SLAVE: slave,
                        CONF_EQUIPMENT: _equipment_data(equipment),
                    }
                )
                return None
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(schema, user_input or self._data),
            errors=errors,
        )

    def _running_client(self, host: str, port: int, slave: int) -> LambdatronicClient | None:
        """The client of the entry being reconfigured if it still talks to the same boiler."""
        if self.source != SOURCE_RECONFIGURE:
            return None
        entry: FroelingConfigEntry = self._get_reconfigure_entry()
        if entry.state is not ConfigEntryState.LOADED or (
            entry.data[CONF_HOST],
            entry.data[CONF_PORT],
            entry.data[CONF_SLAVE],
        ) != (host, port, slave):
            return None
        return entry.runtime_data.client

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Connection to the Modbus TCP gateway."""
        if (form := await self._async_connection_step("user", USER_SCHEMA, user_input)) is not None:
            return form
        assert user_input is not None
        self._name = user_input[CONF_NAME]
        return await self.async_step_equipment()

    async def async_step_equipment(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Confirm the detected equipment."""
        detected = self._data[CONF_EQUIPMENT]
        if user_input is not None:
            self._options = {
                **_equipment_selection(detected, user_input),
                CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
                CONF_PARAMETER_INTERVAL: DEFAULT_PARAMETER_INTERVAL,
            }
            return await self.async_step_cloud()
        return self.async_show_form(step_id="equipment", data_schema=_equipment_schema(detected, detected))

    async def async_step_cloud(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Optional Fröling Connect login; an empty username means without Connect."""
        errors: dict[str, str] = {}
        existing: Mapping[str, Any] = self._data.get(CONF_CLOUD) or {}
        if user_input is not None:
            username = user_input.get(CONF_USERNAME, "").strip()
            password = user_input.get(CONF_PASSWORD, "")
            if not username:
                return self._async_finish(None)
            if not password and username == existing.get(CONF_USERNAME):
                password = existing[CONF_PASSWORD]
            self._credentials = {CONF_USERNAME: username, CONF_PASSWORD: password}
            error, facilities = await _async_check_cloud(self.hass, username, password)
            if error is not None:
                errors["base"] = error
            elif len(facilities) > 1:
                self._facilities = facilities
                return await self.async_step_facility()
            else:
                return self._async_finish(facilities[0].facility_id)
        suggested = {CONF_USERNAME: existing[CONF_USERNAME]} if existing else {}
        return self.async_show_form(
            step_id="cloud",
            data_schema=self.add_suggested_values_to_schema(CLOUD_SCHEMA, user_input or suggested),
            errors=errors,
        )

    async def async_step_facility(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose the facility if the Connect account has several."""
        errors: dict[str, str] = {}
        if user_input is not None:
            facility_id = int(user_input[CONF_FACILITY_ID])
            error, _ = await _async_check_cloud(
                self.hass, self._credentials[CONF_USERNAME], self._credentials[CONF_PASSWORD], facility_id
            )
            if error is None:
                return self._async_finish(facility_id)
            errors["base"] = error
        options = [
            SelectOptionDict(value=str(f.facility_id), label=f.name or str(f.facility_id))
            for f in self._facilities
        ]
        return self.async_show_form(
            step_id="facility",
            data_schema=probatio.Schema(
                {probatio.Required(CONF_FACILITY_ID): SelectSelector(SelectSelectorConfig(options=options))}
            ),
            errors=errors,
        )

    @callback
    def _async_finish(self, facility_id: int | None) -> ConfigFlowResult:
        data = {key: value for key, value in self._data.items() if key != CONF_CLOUD}
        if facility_id is not None:
            data[CONF_CLOUD] = {**self._credentials, CONF_FACILITY_ID: facility_id}
        if self.source == SOURCE_RECONFIGURE:
            entry: FroelingConfigEntry = self._get_reconfigure_entry()
            data[CONF_EQUIPMENT], options = _reconfigured_equipment(
                entry, data[CONF_EQUIPMENT], same_boiler=self.unique_id == entry.unique_id
            )
            return self.async_update_reload_and_abort(
                entry, unique_id=self.unique_id, data=data, options=options
            )
        return self.async_create_entry(title=self._name, data=data, options=self._options)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change host, port, slave and the Fröling Connect login."""
        if not self._data:
            self._data = dict(self._get_reconfigure_entry().data)
        if (
            form := await self._async_connection_step("reconfigure", CONNECTION_SCHEMA, user_input)
        ) is not None:
            return form
        return await self.async_step_cloud()

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Fröling Connect rejected the stored login."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for new Fröling Connect credentials."""
        entry = self._get_reauth_entry()
        cloud = entry.data[CONF_CLOUD]
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            error, _ = await _async_check_cloud(
                self.hass, username, user_input[CONF_PASSWORD], cloud[CONF_FACILITY_ID]
            )
            if error is None:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={
                        CONF_CLOUD: {
                            CONF_USERNAME: username,
                            CONF_PASSWORD: user_input[CONF_PASSWORD],
                            CONF_FACILITY_ID: cloud[CONF_FACILITY_ID],
                        }
                    },
                )
            errors["base"] = error
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(
                REAUTH_SCHEMA, {CONF_USERNAME: cloud[CONF_USERNAME]}
            ),
            errors=errors,
        )


class FroelingOptionsFlow(OptionsFlowWithReload):
    """Enable or disable components and change the polling intervals."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the options."""
        detected = self.config_entry.data[CONF_EQUIPMENT]
        if user_input is not None:
            return self.async_create_entry(
                data={
                    **_equipment_selection(detected, user_input),
                    CONF_SCAN_INTERVAL: user_input[CONF_SCAN_INTERVAL],
                    CONF_PARAMETER_INTERVAL: user_input[CONF_PARAMETER_INTERVAL],
                }
            )
        options = self.config_entry.options
        schema = _equipment_schema(detected, options).extend(
            {
                probatio.Required(
                    CONF_SCAN_INTERVAL, default=options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                ): _box(MIN_SCAN_INTERVAL, MAX_SCAN_INTERVAL, "s"),
                probatio.Required(
                    CONF_PARAMETER_INTERVAL,
                    default=options.get(CONF_PARAMETER_INTERVAL, DEFAULT_PARAMETER_INTERVAL),
                ): _box(MIN_PARAMETER_INTERVAL, MAX_PARAMETER_INTERVAL, "min"),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
