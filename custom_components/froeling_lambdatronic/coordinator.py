"""Data update coordinators of the Fröling Lambdatronic integration."""

import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from froeling_lambdatronic import (
    AuthenticationError,
    CloudBoilerState,
    CloudComponent,
    CloudComponentDetail,
    CloudError,
    CloudHeader,
    CloudSnapshot,
    CloudSystemInfo,
    ComponentType,
    Equipment,
    FroelingConnectClient,
    FroelingError,
    LambdatronicClient,
    LambdatronicParameters,
    LambdatronicValues,
    RateLimitedError,
    build_snapshot,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CLOUD_INFO_INTERVAL,
    CONF_CLOUD_INTERVAL,
    CONF_PARAMETER_INTERVAL,
    CONF_SCAN_INTERVAL,
    DEFAULT_CLOUD_INTERVAL,
    DEFAULT_PARAMETER_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ISSUE_REMOTE_CONTROL_DISABLED,
    MODEL,
    room_sensor_circuits,
)

if TYPE_CHECKING:
    from .sources import FroelingSources

_LOGGER = logging.getLogger(__name__)


async def async_detect_room_sensors(
    client: FroelingConnectClient,
    facility_id: int,
    components: Iterable[CloudComponent],
    known: Mapping[str, bool | None] | None = None,
) -> list[int] | None:
    """Sorted numbers of the heating circuits with a room sensor; None if a component detail failed.

    `known` maps heating circuit keys to what Fröling Connect already told (the room sensors of a
    snapshot); otherwise `CloudComponent.room_sensor` of the component list counts. Only for a
    heating circuit still unknown the picture URL of its component detail is read. Only a known
    True counts as a room sensor.
    """
    room_sensors: dict[str, bool | None] = {}
    for component in components:
        if component.type is not ComponentType.CIRCUIT or component.key is None:
            continue
        present = component.room_sensor if known is None else known.get(component.key)
        if present is None:
            try:
                detail = await client.get_component_detail(facility_id, component.component_id)
            except FroelingError as err:
                _LOGGER.debug("Room sensor of %s not read, Fröling Connect failed: %s", component.key, err)
                return None
            present = detail.component.room_sensor
        room_sensors[component.key] = present
    return room_sensor_circuits(room_sensors)


@dataclass
class FroelingRuntimeData:
    """Objects shared by all platforms of one config entry; Modbus or cloud parts are None if not used."""

    modbus: LambdatronicClient | None
    cloud_client: FroelingConnectClient | None
    facility_id: int | None
    equipment: Equipment
    boiler_device_id: str
    values: ModbusValuesCoordinator | None
    parameters: ModbusParametersCoordinator | None
    cloud: CloudCoordinator | None
    cloud_info: CloudInfoCoordinator | None
    sources: FroelingSources


type FroelingConfigEntry = ConfigEntry[FroelingRuntimeData]


def _modbus_failed(err: FroelingError) -> UpdateFailed:
    return UpdateFailed(
        translation_domain=DOMAIN,
        translation_key="update_failed",
        translation_placeholders={"error": str(err)},
    )


def _cloud_failed(err: FroelingError) -> Exception:
    """Translate a Fröling Connect error; HA waits as long as a rate limit asks for."""
    if isinstance(err, AuthenticationError):
        return ConfigEntryAuthFailed(translation_domain=DOMAIN, translation_key="cloud_auth_failed")
    return UpdateFailed(
        translation_domain=DOMAIN,
        translation_key="cloud_update_failed",
        translation_placeholders={"error": str(err)},
        retry_after=err.retry_after if isinstance(err, RateLimitedError) else None,
    )


class ModbusValuesCoordinator(DataUpdateCoordinator[LambdatronicValues]):
    """Polls the current values via Modbus (Werte-Takt)."""

    config_entry: FroelingConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: FroelingConfigEntry,
        client: LambdatronicClient,
        equipment: Equipment,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} values",
            update_interval=timedelta(seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
        )
        self.client = client
        self.equipment = equipment

    async def _async_update_data(self) -> LambdatronicValues:
        try:
            return await self.client.read_values(self.equipment)
        except FroelingError as err:
            raise _modbus_failed(err) from err


class ModbusParametersCoordinator(DataUpdateCoordinator[LambdatronicParameters]):
    """Polls the settings via Modbus (Parameter-Takt) and after every write."""

    config_entry: FroelingConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: FroelingConfigEntry,
        client: LambdatronicClient,
        equipment: Equipment,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} parameters",
            update_interval=timedelta(
                minutes=entry.options.get(CONF_PARAMETER_INTERVAL, DEFAULT_PARAMETER_INTERVAL)
            ),
        )
        self.client = client
        self.equipment = equipment

    async def _async_update_data(self) -> LambdatronicParameters:
        try:
            return await self.client.read_parameters(self.equipment)
        except FroelingError as err:
            raise _modbus_failed(err) from err


@dataclass(frozen=True, slots=True)
class CloudData:
    """Values, remote control permission and error history read from Fröling Connect."""

    snapshot: CloudSnapshot
    # Component details by component key ('boiler', 'hc1', ...); only without Modbus.
    details: Mapping[str, CloudComponentDetail]
    boiler_state: CloudBoilerState
    errors: tuple[CloudError, ...]

    @property
    def remote_control_allowed(self) -> bool:
        """True if 'Fernschalten über connect möglich' is enabled."""
        return self.boiler_state.remote_control_allowed


class CloudCoordinator(DataUpdateCoordinator[CloudData]):
    """Polls Fröling Connect (Cloud-Takt).

    With Modbus only the overview is read (one request); without Modbus every component
    detail as well, except those of child devices the user disabled. The details are read here
    instead of with `read_snapshot`, because they also carry the limits of the settings.
    """

    config_entry: FroelingConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: FroelingConfigEntry,
        client: FroelingConnectClient,
        facility_id: int,
        *,
        details: bool,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} cloud",
            update_interval=timedelta(minutes=entry.options.get(CONF_CLOUD_INTERVAL, DEFAULT_CLOUD_INTERVAL)),
        )
        self.client = client
        self.facility_id = facility_id
        self.details = details
        # Component keys ('hc2', 'buffer1', ...) skipped by the last update.
        self.skipped: frozenset[str] = frozenset()

    async def _async_update_data(self) -> CloudData:
        skip = self._disabled_components() if self.details else frozenset()
        try:
            overview = await self.client.get_overview(self.facility_id)
            details: list[CloudComponentDetail] = []
            if self.details:
                for component in await self.client.get_components(self.facility_id):
                    if component.key is not None and component.key not in skip:
                        details.append(
                            await self.client.get_component_detail(self.facility_id, component.component_id)
                        )
            state = await self.client.get_boiler_state(self.facility_id)
            errors = await self.client.get_errors(self.facility_id)
        except FroelingError as err:
            raise _cloud_failed(err) from err
        self.skipped = skip
        if state.remote_control_allowed:
            ir.async_delete_issue(
                self.hass, DOMAIN, f"{ISSUE_REMOTE_CONTROL_DISABLED}_{self.config_entry.entry_id}"
            )
        else:
            async_create_remote_control_issue(self.hass, self.config_entry)
        return CloudData(
            build_snapshot(overview, details),
            {detail.component.key: detail for detail in details if detail.component.key is not None},
            state,
            tuple(errors),
        )

    def _disabled_components(self) -> frozenset[str]:
        """Component keys of the child devices that are disabled in the device registry."""
        entry_id = self.config_entry.entry_id
        registry = dr.async_get(self.hass)
        return frozenset(
            identifier.removeprefix(f"{entry_id}_")
            for device in dr.async_child_entries_for_config_entry(registry, entry_id)
            if device.disabled
            for domain, identifier in device.identifiers
            if domain == DOMAIN and identifier.startswith(f"{entry_id}_")
        )


@dataclass(frozen=True, slots=True)
class CloudInfo:
    """System information and connection state of the facility."""

    system: CloudSystemInfo
    header: CloudHeader


class CloudInfoCoordinator(DataUpdateCoordinator[CloudInfo]):
    """Reads system information and connection state hourly; keeps the boiler device up to date."""

    config_entry: FroelingConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: FroelingConfigEntry,
        client: FroelingConnectClient,
        facility_id: int,
        boiler_device_id: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} cloud info",
            update_interval=CLOUD_INFO_INTERVAL,
        )
        self.client = client
        self.facility_id = facility_id
        self.boiler_device_id = boiler_device_id

    async def _async_update_data(self) -> CloudInfo:
        try:
            system = await self.client.get_system_info(self.facility_id)
            header = await self.client.get_header(self.facility_id)
        except FroelingError as err:
            raise _cloud_failed(err) from err
        self._async_update_boiler_device(system)
        return CloudInfo(system, header)

    @callback
    def _async_update_boiler_device(self, system: CloudSystemInfo) -> None:
        dr.async_get(self.hass).async_update_device(
            self.boiler_device_id,
            model=system.product_type or MODEL,
            serial_number=system.equipment_number,
            sw_version=system.core_module_version,
            hw_version=system.touch_version,
        )


@callback
def async_create_remote_control_issue(hass: HomeAssistant, entry: FroelingConfigEntry) -> None:
    """Tell the user to enable 'Fernschalten über connect möglich'."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"{ISSUE_REMOTE_CONTROL_DISABLED}_{entry.entry_id}",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_REMOTE_CONTROL_DISABLED,
        translation_placeholders={"name": entry.title},
    )
