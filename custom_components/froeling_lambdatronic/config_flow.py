"""Config flow of the Fröling Lambdatronic integration: Modbus, Fröling Connect or both."""

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
    equipment_from_components,
    merge_equipment,
)
from homeassistant.config_entries import (
    SOURCE_RECONFIGURE,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.translation import async_get_translations

from .const import (
    CONF_BUFFERS,
    CONF_CIRCULATION_PUMP,
    CONF_CLOUD,
    CONF_CLOUD_INTERVAL,
    CONF_EQUIPMENT,
    CONF_FACILITY_ID,
    CONF_HEATING_CIRCUITS,
    CONF_HOT_WATER,
    CONF_PARAMETER_INTERVAL,
    CONF_ROOM_SENSORS,
    CONF_SCAN_INTERVAL,
    CONF_SLAVE,
    DEFAULT_CLOUD_INTERVAL,
    DEFAULT_PARAMETER_INTERVAL,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_SLAVE,
    DOMAIN,
    MAX_CLOUD_INTERVAL,
    MAX_PARAMETER_INTERVAL,
    MAX_SCAN_INTERVAL,
    MIN_CLOUD_INTERVAL,
    MIN_PARAMETER_INTERVAL,
    MIN_SCAN_INTERVAL,
    ConnectionType,
    connection_type,
)
from .coordinator import FroelingConfigEntry, async_detect_room_sensors

_LOGGER = logging.getLogger(__name__)

CONF_ADVANCED = "advanced"
TITLE = "Fröling"


def _box(minimum: int, maximum: int, unit: str | None = None) -> probatio.All[int]:
    config = NumberSelectorConfig(min=minimum, max=maximum, step=1, mode=NumberSelectorMode.BOX)
    if unit is not None:
        config["unit_of_measurement"] = unit
    return probatio.All(NumberSelector(config), probatio.Coerce(int))


MODBUS_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_HOST): TextSelector(),
        probatio.Required(CONF_ADVANCED): section(
            probatio.Schema(
                {
                    probatio.Required(CONF_PORT, default=DEFAULT_PORT): _box(1, 65535),
                    probatio.Required(CONF_SLAVE, default=DEFAULT_SLAVE): _box(1, 247),
                }
            ),
            {"collapsed": True},
        ),
    }
)
EMAIL = TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL))
PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
CLOUD_SCHEMA = probatio.Schema(
    {probatio.Required(CONF_USERNAME): EMAIL, probatio.Required(CONF_PASSWORD): PASSWORD}
)
# Reconfiguring with the same username may keep the stored password.
RECONFIGURE_CLOUD_SCHEMA = probatio.Schema(
    {probatio.Required(CONF_USERNAME): EMAIL, probatio.Optional(CONF_PASSWORD): PASSWORD}
)


def equipment_data(equipment: Equipment) -> dict[str, Any]:
    """Equipment as stored in the entry data."""
    return {
        CONF_HEATING_CIRCUITS: sorted(equipment.heating_circuits),
        CONF_HOT_WATER: sorted(equipment.hot_water),
        CONF_BUFFERS: sorted(equipment.buffers),
        CONF_CIRCULATION_PUMP: equipment.circulation_pump,
    }


def _modbus_error(err: FroelingError) -> str:
    if isinstance(err, NoResponseError):
        return "no_response"
    if isinstance(err, CommunicationError):
        return "invalid_slave"
    return "cannot_connect"


def _cloud_error(err: FroelingError) -> str:
    return "invalid_auth" if isinstance(err, AuthenticationError) else "cloud_cannot_connect"


class FroelingConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a Lambdatronic boiler via Modbus, Fröling Connect or both."""

    VERSION = 2

    def __init__(self) -> None:
        self._hybrid = False
        self._modbus: dict[str, Any] | None = None
        self._cloud: dict[str, Any] | None = None
        self._modbus_equipment: Equipment | None = None
        self._cloud_equipment: Equipment | None = None
        # Heating circuits with a room sensor, read with the Connect components; None if a detail
        # read failed (the setup detects them then).
        self._room_sensors: list[int] | None = []
        self._cloud_client: FroelingConnectClient | None = None
        self._facilities: list[Facility] = []
        self._title = TITLE

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: FroelingConfigEntry) -> FroelingOptionsFlow:
        """Options: the polling intervals of the used sources."""
        return FroelingOptionsFlow()

    # Setup

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose how the boiler is connected."""
        return self.async_show_menu(
            menu_options=[ConnectionType.MODBUS, ConnectionType.CLOUD, ConnectionType.HYBRID]
        )

    async def async_step_hybrid(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Modbus first, then Fröling Connect."""
        self._hybrid = True
        return await self.async_step_modbus()

    async def async_step_modbus(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Host of the Modbus TCP gateway; port and slave ID in the advanced section."""
        return await self._async_modbus_step("modbus", user_input)

    async def async_step_cloud(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Fröling Connect login."""
        return await self._async_cloud_step("cloud", CLOUD_SCHEMA, user_input)

    async def async_step_facility(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose the facility if the Fröling Connect account has several."""
        errors: dict[str, str] = {}
        if user_input is not None:
            facility_id = int(user_input[CONF_FACILITY_ID])
            facility = next(f for f in self._facilities if f.facility_id == facility_id)
            if (result := await self._async_use_facility(facility, errors)) is not None:
                return result
        options = [
            SelectOptionDict(
                value=str(f.facility_id),
                label=f"{f.name} ({f.product_type})" if f.product_type else f.name or str(f.facility_id),
            )
            for f in self._facilities
        ]
        return self.async_show_form(
            step_id="facility",
            data_schema=probatio.Schema(
                {probatio.Required(CONF_FACILITY_ID): SelectSelector(SelectSelectorConfig(options=options))}
            ),
            errors=errors,
        )

    async def async_step_mismatch(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Modbus and Fröling Connect found different components; all of them are used."""
        if user_input is not None:
            return self._async_save()
        assert self._modbus_equipment is not None
        assert self._cloud_equipment is not None
        _, deviations = merge_equipment(self._modbus_equipment, self._cloud_equipment)
        return self.async_show_form(
            step_id="mismatch",
            data_schema=probatio.Schema({}),
            description_placeholders={"components": await self._async_component_names(deviations)},
        )

    async def _async_modbus_step(self, step_id: str, user_input: dict[str, Any] | None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            port = user_input[CONF_ADVANCED][CONF_PORT]
            slave = user_input[CONF_ADVANCED][CONF_SLAVE]
            await self.async_set_unique_id(f"{host}:{port}:{slave}")
            if self.source != SOURCE_RECONFIGURE or self.unique_id != self._get_reconfigure_entry().unique_id:
                self._abort_if_unique_id_configured()
            try:
                self._modbus_equipment = await self._async_detect(host, port, slave)
            except FroelingError as err:
                errors["base"] = _modbus_error(err)
            except Exception:
                _LOGGER.exception("Unexpected error while connecting to %s", host)
                errors["base"] = "unknown"
            else:
                self._modbus = {CONF_HOST: host, CONF_PORT: port, CONF_SLAVE: slave}
                if self._hybrid and self._cloud is None:
                    return await self.async_step_cloud()
                if (result := await self._async_finish(errors)) is not None:
                    return result
        suggested = user_input or (
            {}
            if self._modbus is None
            else {
                CONF_HOST: self._modbus[CONF_HOST],
                CONF_ADVANCED: {CONF_PORT: self._modbus[CONF_PORT], CONF_SLAVE: self._modbus[CONF_SLAVE]},
            }
        )
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(MODBUS_SCHEMA, suggested),
            errors=errors,
        )

    async def _async_cloud_step(
        self, step_id: str, schema: probatio.Schema, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        stored = self._cloud or {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip()
            password = user_input.get(CONF_PASSWORD) or ""
            if not password and username == stored.get(CONF_USERNAME):
                password = stored[CONF_PASSWORD]
            client = FroelingConnectClient(async_get_clientsession(self.hass), username, password)
            try:
                await client.login()
                facilities = await client.get_facilities()
            except FroelingError as err:
                errors["base"] = _cloud_error(err)
            except Exception:
                _LOGGER.exception("Unexpected error while logging in to Fröling Connect")
                errors["base"] = "unknown"
            else:
                if not facilities:
                    errors["base"] = "no_facilities"
                else:
                    self._cloud_client = client
                    self._cloud = {CONF_USERNAME: username, CONF_PASSWORD: password}
                    self._facilities = facilities
                    if len(facilities) > 1:
                        return await self.async_step_facility()
                    if (result := await self._async_use_facility(facilities[0], errors)) is not None:
                        return result
        suggested = user_input or ({CONF_USERNAME: stored[CONF_USERNAME]} if stored else {})
        return self.async_show_form(
            step_id=step_id,
            data_schema=self.add_suggested_values_to_schema(schema, suggested),
            errors=errors,
        )

    async def _async_use_facility(
        self, facility: Facility, errors: dict[str, str]
    ) -> ConfigFlowResult | None:
        """Read the components of the chosen facility; a form result is returned by the caller on error."""
        assert self._cloud is not None
        if self._facility_in_use(facility.facility_id):
            return self.async_abort(reason="already_configured")
        if self._modbus is None:
            await self.async_set_unique_id(f"cloud:{facility.facility_id}")
        self._cloud[CONF_FACILITY_ID] = facility.facility_id
        self._cloud_equipment = None
        # Names from Fröling Connect are never adopted, only the boiler type.
        self._title = f"{TITLE} {facility.product_type}" if facility.product_type else TITLE
        return await self._async_finish(errors)

    def _facility_in_use(self, facility_id: int) -> bool:
        """True if another entry already uses this Fröling Connect facility."""
        own = self._get_reconfigure_entry().entry_id if self.source == SOURCE_RECONFIGURE else None
        return any(
            entry.entry_id != own and (entry.data.get(CONF_CLOUD) or {}).get(CONF_FACILITY_ID) == facility_id
            for entry in self._async_current_entries(include_ignore=False)
        )

    async def _async_finish(self, errors: dict[str, str]) -> ConfigFlowResult | None:
        """Detect what is still missing, compare both sources and save; None with `errors` set on failure."""
        if (error := await self._async_detect_missing()) is not None:
            errors["base"] = error
            return None
        if self._modbus_equipment is not None and self._cloud_equipment is not None:
            _, deviations = merge_equipment(self._modbus_equipment, self._cloud_equipment)
            if deviations:
                return await self.async_step_mismatch()
        return self._async_save()

    async def _async_detect_missing(self) -> str | None:
        """Detect the equipment of every configured source not detected in this flow yet."""
        if self._modbus is not None and self._modbus_equipment is None:
            try:
                self._modbus_equipment = await self._async_detect(
                    self._modbus[CONF_HOST], self._modbus[CONF_PORT], self._modbus[CONF_SLAVE]
                )
            except FroelingError as err:
                return _modbus_error(err)
        if self._cloud is not None and self._cloud_equipment is None:
            client = self._cloud_client or FroelingConnectClient(
                async_get_clientsession(self.hass), self._cloud[CONF_USERNAME], self._cloud[CONF_PASSWORD]
            )
            try:
                components = await client.get_components(self._cloud[CONF_FACILITY_ID])
            except FroelingError as err:
                return _cloud_error(err)
            self._cloud_equipment = equipment_from_components(components)
            # A failed detail read does not fail the flow: the setup detects the room sensors then.
            self._room_sensors = await async_detect_room_sensors(
                client, self._cloud[CONF_FACILITY_ID], components
            )
        return None

    async def _async_detect(self, host: str, port: int, slave: int) -> Equipment:
        """Read the system state and detect the equipment.

        The running client of the reconfigured entry is reused for the same boiler: the gateway
        mixes up answers when two clients talk to it at the same time.
        """
        if (running := self._running_client(host, port, slave)) is not None:
            await running.probe()
            return await running.detect_equipment()
        client = LambdatronicClient(host, port, slave)
        try:
            await client.connect()
            await client.probe()
            return await client.detect_equipment()
        finally:
            await client.close()

    def _running_client(self, host: str, port: int, slave: int) -> LambdatronicClient | None:
        if self.source != SOURCE_RECONFIGURE:
            return None
        entry: FroelingConfigEntry = self._get_reconfigure_entry()
        if entry.state is not ConfigEntryState.LOADED or (
            entry.data.get(CONF_HOST),
            entry.data.get(CONF_PORT),
            entry.data.get(CONF_SLAVE),
        ) != (host, port, slave):
            return None
        return entry.runtime_data.modbus

    async def _async_component_names(self, keys: tuple[str, ...]) -> str:
        """Names of components ('hc3', 'circulation') as the device names in the user's language."""
        translations = await async_get_translations(self.hass, self.hass.config.language, "device", [DOMAIN])
        names: list[str] = []
        for key in keys:
            if key == "circulation":
                kind, number = "circulation_pump", ""
            else:
                prefix = key.rstrip("0123456789")
                kind = {"hc": "heating_circuit", "dhw": "hot_water", "buffer": "buffer"}[prefix]
                number = f"{int(key[len(prefix) :]):02d}"
            template = translations.get(f"component.{DOMAIN}.device.{kind}.name", kind)
            names.append(template.replace("{number}", number))
        return ", ".join(names)

    @callback
    def _async_save(self) -> ConfigFlowResult:
        """Create the entry, or update the reconfigured one."""
        equipment: Equipment | None = None
        for detected in (self._modbus_equipment, self._cloud_equipment):
            if detected is not None:
                equipment = detected if equipment is None else merge_equipment(equipment, detected)[0]
        assert equipment is not None
        data: dict[str, Any] = {**(self._modbus or {}), CONF_EQUIPMENT: equipment_data(equipment)}
        # Only Fröling Connect tells whether a room sensor is connected.
        if self._cloud is None:
            data[CONF_ROOM_SENSORS] = []
        elif self._room_sensors is not None:
            data[CONF_ROOM_SENSORS] = self._room_sensors
        if self._cloud is not None:
            data[CONF_CLOUD] = dict(self._cloud)
        if self.source == SOURCE_RECONFIGURE:
            return self.async_update_reload_and_abort(
                self._get_reconfigure_entry(), unique_id=self.unique_id, data=data
            )
        return self.async_create_entry(title=self._title, data=data, options=_default_options(data))

    # Reconfigure

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Change or add a connection, or remove one of two."""
        entry = self._get_reconfigure_entry()
        if CONF_HOST in entry.data:
            self._modbus = {key: entry.data[key] for key in (CONF_HOST, CONF_PORT, CONF_SLAVE)}
        if (cloud := entry.data.get(CONF_CLOUD)) is not None:
            self._cloud = dict(cloud)
        await self.async_set_unique_id(entry.unique_id)
        options = ["reconfigure_modbus", "reconfigure_cloud"]
        if connection_type(entry.data) is ConnectionType.HYBRID:
            options += ["remove_cloud", "remove_modbus"]
        return self.async_show_menu(step_id="reconfigure", menu_options=options)

    async def async_step_reconfigure_modbus(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change or add the Modbus connection."""
        return await self._async_modbus_step("reconfigure_modbus", user_input)

    async def async_step_reconfigure_cloud(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Add Fröling Connect or change its login."""
        return await self._async_cloud_step("reconfigure_cloud", RECONFIGURE_CLOUD_SCHEMA, user_input)

    async def async_step_remove_cloud(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Use Modbus only."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._cloud = None
            if (result := await self._async_finish(errors)) is not None:
                return result
        return self.async_show_form(step_id="remove_cloud", data_schema=probatio.Schema({}), errors=errors)

    async def async_step_remove_modbus(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Use Fröling Connect only."""
        errors: dict[str, str] = {}
        if user_input is not None:
            assert self._cloud is not None
            await self.async_set_unique_id(f"cloud:{self._cloud[CONF_FACILITY_ID]}")
            self._abort_if_unique_id_configured()
            self._modbus = None
            if (result := await self._async_finish(errors)) is not None:
                return result
        return self.async_show_form(step_id="remove_modbus", data_schema=probatio.Schema({}), errors=errors)

    # Reauthentication

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
            client = FroelingConnectClient(
                async_get_clientsession(self.hass), username, user_input[CONF_PASSWORD]
            )
            try:
                await client.login()
                facilities = await client.get_facilities()
            except FroelingError as err:
                errors["base"] = _cloud_error(err)
            except Exception:
                _LOGGER.exception("Unexpected error while logging in to Fröling Connect")
                errors["base"] = "unknown"
            else:
                if all(f.facility_id != cloud[CONF_FACILITY_ID] for f in facilities):
                    errors["base"] = "facility_missing"
                else:
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
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=self.add_suggested_values_to_schema(
                CLOUD_SCHEMA, {CONF_USERNAME: cloud[CONF_USERNAME]}
            ),
            errors=errors,
        )


def _default_options(data: Mapping[str, Any]) -> dict[str, int]:
    """Default intervals of the sources the entry uses."""
    options: dict[str, int] = {}
    if CONF_HOST in data:
        options[CONF_SCAN_INTERVAL] = DEFAULT_SCAN_INTERVAL
        options[CONF_PARAMETER_INTERVAL] = DEFAULT_PARAMETER_INTERVAL
    if CONF_CLOUD in data:
        options[CONF_CLOUD_INTERVAL] = DEFAULT_CLOUD_INTERVAL
    return options


class FroelingOptionsFlow(OptionsFlowWithReload):
    """Polling intervals of the sources the entry uses."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the intervals."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        fields: dict[probatio.Marker, Any] = {}
        if CONF_HOST in self.config_entry.data:
            fields[probatio.Required(CONF_SCAN_INTERVAL)] = _box(MIN_SCAN_INTERVAL, MAX_SCAN_INTERVAL, "s")
            fields[probatio.Required(CONF_PARAMETER_INTERVAL)] = _box(
                MIN_PARAMETER_INTERVAL, MAX_PARAMETER_INTERVAL, "min"
            )
        if CONF_CLOUD in self.config_entry.data:
            fields[probatio.Required(CONF_CLOUD_INTERVAL)] = _box(
                MIN_CLOUD_INTERVAL, MAX_CLOUD_INTERVAL, "min"
            )
        suggested = {**_default_options(self.config_entry.data), **self.config_entry.options}
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(probatio.Schema(fields), suggested),
        )
