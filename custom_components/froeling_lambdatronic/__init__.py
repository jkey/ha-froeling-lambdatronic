"""The Fröling Lambdatronic integration."""

import logging
import re
from collections.abc import Mapping
from typing import Any

from froeling_lambdatronic import Equipment, FroelingConnectClient, FroelingError, LambdatronicClient
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .const import (
    CONF_BUFFERS,
    CONF_CIRCULATION_PUMP,
    CONF_CLOUD,
    CONF_CLOUD_INTERVAL,
    CONF_EQUIPMENT,
    CONF_FACILITY_ID,
    CONF_HEATING_CIRCUITS,
    CONF_HOT_WATER,
    CONF_ROOM_SENSORS,
    CONF_SLAVE,
    DEFAULT_CLOUD_INTERVAL,
    DOMAIN,
    ISSUE_PROTOCOL_DISABLED,
    ISSUE_REMOTE_CONTROL_DISABLED,
    MANUFACTURER,
    MODEL,
)
from .coordinator import (
    CloudCoordinator,
    CloudInfoCoordinator,
    FroelingConfigEntry,
    FroelingRuntimeData,
    ModbusParametersCoordinator,
    ModbusValuesCoordinator,
    async_detect_room_sensors,
)
from .entity import DEVICE_TRANSLATION_KEYS, ComponentKind, child_device_info, components
from .services import async_setup_services
from .sources import FroelingSources

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
    Platform.WATER_HEATER,
]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def equipment_from_data(data: Mapping[str, Any]) -> Equipment:
    """The equipment stored in the entry data."""
    return Equipment(
        heating_circuits=tuple(data[CONF_HEATING_CIRCUITS]),
        hot_water=tuple(data[CONF_HOT_WATER]),
        buffers=tuple(data[CONF_BUFFERS]),
        circulation_pump=data[CONF_CIRCULATION_PUMP],
    )


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the actions of the integration."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: FroelingConfigEntry) -> bool:
    """Set up Fröling Lambdatronic from a config entry (Modbus, Fröling Connect or both)."""
    equipment = equipment_from_data(entry.data[CONF_EQUIPMENT])
    cloud_config: Mapping[str, Any] | None = entry.data.get(CONF_CLOUD)
    if cloud_config is None:
        _async_remove_cloud_leftovers(hass, entry)

    modbus: LambdatronicClient | None = None
    values: ModbusValuesCoordinator | None = None
    parameters: ModbusParametersCoordinator | None = None
    if CONF_HOST in entry.data:
        modbus = LambdatronicClient(entry.data[CONF_HOST], entry.data[CONF_PORT], entry.data[CONF_SLAVE])
        values = ModbusValuesCoordinator(hass, entry, modbus, equipment)
        parameters = ModbusParametersCoordinator(hass, entry, modbus, equipment)
        try:
            await modbus.connect()
            await values.async_config_entry_first_refresh()
            await parameters.async_config_entry_first_refresh()
        except FroelingError as err:
            await modbus.close()
            raise ConfigEntryNotReady(
                translation_domain=DOMAIN,
                translation_key="cannot_connect",
                translation_placeholders={"host": entry.data[CONF_HOST], "error": str(err)},
            ) from err
        except ConfigEntryNotReady:
            await modbus.close()
            raise

    registry = dr.async_get(hass)
    boiler = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer=MANUFACTURER,
    )
    if cloud_config is None:
        # With Fröling Connect the system information sets model, serial number and versions.
        registry.async_update_device(
            boiler.id, model=MODEL, serial_number=None, sw_version=None, hw_version=None
        )

    cloud_client: FroelingConnectClient | None = None
    cloud: CloudCoordinator | None = None
    cloud_info: CloudInfoCoordinator | None = None
    facility_id: int | None = None
    if cloud_config is not None:
        facility_id = cloud_config[CONF_FACILITY_ID]
        cloud_client = FroelingConnectClient(
            async_get_clientsession(hass), cloud_config[CONF_USERNAME], cloud_config[CONF_PASSWORD]
        )
        cloud = CloudCoordinator(hass, entry, cloud_client, facility_id, details=modbus is None)
        cloud_info = CloudInfoCoordinator(hass, entry, cloud_client, facility_id, boiler.id)
        if modbus is None:
            await cloud.async_config_entry_first_refresh()
        else:
            # Next to Modbus the cloud is optional: it never blocks or fails the setup.
            await cloud.async_refresh()
        await cloud_info.async_refresh()
        if CONF_ROOM_SENSORS not in entry.data:
            # Before the platforms: the climate entities read the room sensors from the entry data.
            await _async_detect_room_sensors(hass, entry, cloud)

    entry.runtime_data = FroelingRuntimeData(
        modbus=modbus,
        cloud_client=cloud_client,
        facility_id=facility_id,
        equipment=equipment,
        boiler_device_id=boiler.id,
        values=values,
        parameters=parameters,
        cloud=cloud,
        cloud_info=cloud_info,
        sources=FroelingSources(
            hass,
            entry,
            modbus=modbus,
            values=values,
            parameters=parameters,
            cloud_client=cloud_client,
            cloud=cloud,
            cloud_info=cloud_info,
            facility_id=facility_id,
        ),
    )
    _async_update_child_devices(hass, entry, equipment)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_detect_room_sensors(
    hass: HomeAssistant, entry: FroelingConfigEntry, cloud: CloudCoordinator
) -> None:
    """Detect the room sensors of an entry without stored ones once and store them in the entry data.

    The first refresh already tells most heating circuits (with Connect only from the component
    details); only the detail of a circuit still unknown is read. Nothing is stored if Fröling
    Connect fails: no room sensor for this start, the next start tries again. There is no update
    listener, so storing does not reload the entry.
    """
    if cloud.data is None:
        _LOGGER.debug("Room sensors not detected, Fröling Connect failed")
        return
    snapshot = cloud.data.snapshot
    detected = await async_detect_room_sensors(
        cloud.client, cloud.facility_id, snapshot.components, snapshot.room_sensors
    )
    if detected is not None:
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_ROOM_SENSORS: detected})


async def async_migrate_entry(hass: HomeAssistant, entry: FroelingConfigEntry) -> bool:
    """Migrate an entry of 0.1.0 (version 1) to version 2; HA refuses newer versions itself."""
    if entry.version == 1:
        _async_migrate_v1_devices(hass, entry)
        _async_migrate_v1_entities(hass, entry)
        options = {key: value for key, value in entry.options.items() if key not in _V1_SELECTION}
        if entry.data.get(CONF_CLOUD) is not None:
            options.setdefault(CONF_CLOUD_INTERVAL, DEFAULT_CLOUD_INTERVAL)
        equipment = {CONF_CIRCULATION_PUMP: False, **entry.data[CONF_EQUIPMENT]}
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_EQUIPMENT: equipment}, options=options, version=2
        )
    return True


_V1_SELECTION = (CONF_HEATING_CIRCUITS, CONF_HOT_WATER, CONF_BUFFERS)
# Entities of 0.1.0 replaced in 0.2.0: heating circuit mode (now climate), tank setpoint and
# temperature (now water_heater), pellet counter kg part (now the combined pellet counter), system
# state (now the system operating mode select).
_V1_REMOVED = (
    (Platform.SELECT, re.compile(r"hc\d+_operating_mode")),
    (Platform.NUMBER, re.compile(r"dhw\d+_setpoint")),
    (Platform.SENSOR, re.compile(r"pellet_counter_kg")),
    (Platform.SENSOR, re.compile(r"dhw\d+_temperature")),
    (Platform.SENSOR, re.compile(r"system_state")),
)
_V1_RENAMED = {"boiler_output": "boiler_control_output"}


@callback
def _async_migrate_v1_devices(hass: HomeAssistant, entry: FroelingConfigEntry) -> None:
    """Components deselected in the 0.1.0 options become disabled devices."""
    detected = entry.data[CONF_EQUIPMENT]

    def unselected(key: str) -> tuple[int, ...]:
        selected = entry.options.get(key, detected[key])
        return tuple(n for n in detected[key] if n not in selected)

    deselected = Equipment(
        heating_circuits=unselected(CONF_HEATING_CIRCUITS),
        hot_water=unselected(CONF_HOT_WATER),
        buffers=unselected(CONF_BUFFERS),
    )
    if not (deselected.heating_circuits or deselected.hot_water or deselected.buffers):
        return
    registry = dr.async_get(hass)
    boiler = registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, entry.entry_id)}, name=entry.title
    )
    for component in components(deselected):
        if component.kind is not ComponentKind.BOILER:
            child = registry.async_get_or_create_child(
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, component.identifier(entry.entry_id))},
                parent_device_id=boiler.id,
                disabled_by=dr.DeviceEntryDisabler.USER,
                translation_key=DEVICE_TRANSLATION_KEYS[component.kind],
                translation_placeholders={"number": f"{component.number:02d}"},
            )
            # 0.1.0 deleted deselected devices; HA restores a deleted device with its stored
            # disabled_by (None) and ignores the one passed above.
            if child.disabled_by is None:
                registry.async_update_child_device(child.id, disabled_by=dr.DeviceEntryDisabler.USER)


@callback
def _async_migrate_v1_entities(hass: HomeAssistant, entry: FroelingConfigEntry) -> None:
    """Remove replaced entities and give the boiler control output its new unique ID."""
    registry = er.async_get(hass)
    prefix = f"{entry.entry_id}_"
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        key = entity.unique_id.removeprefix(prefix)
        if any(entity.domain == platform and pattern.fullmatch(key) for platform, pattern in _V1_REMOVED):
            registry.async_remove(entity.entity_id)
        elif (new_key := _V1_RENAMED.get(key)) is not None and entity.domain == Platform.SENSOR:
            registry.async_update_entity(entity.entity_id, new_unique_id=prefix + new_key)


async def async_unload_entry(hass: HomeAssistant, entry: FroelingConfigEntry) -> bool:
    """Unload a config entry."""
    if (unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS)) and (
        modbus := entry.runtime_data.modbus
    ) is not None:
        await modbus.close()
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: FroelingConfigEntry) -> None:
    """Delete the repair issues of a removed entry."""
    for issue in (ISSUE_REMOTE_CONTROL_DISABLED, ISSUE_PROTOCOL_DISABLED):
        ir.async_delete_issue(hass, DOMAIN, f"{issue}_{entry.entry_id}")


@callback
def _async_remove_cloud_leftovers(hass: HomeAssistant, entry: FroelingConfigEntry) -> None:
    """Remove the repair issue and the system mode select of a removed Fröling Connect login."""
    ir.async_delete_issue(hass, DOMAIN, f"{ISSUE_REMOTE_CONTROL_DISABLED}_{entry.entry_id}")
    registry = er.async_get(hass)
    if entity_id := registry.async_get_entity_id(
        Platform.SELECT, DOMAIN, f"{entry.entry_id}_system_operating_mode"
    ):
        registry.async_remove(entity_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: FroelingConfigEntry, device: dr.AnyDeviceEntry
) -> bool:
    """Allow deleting devices of components that are no longer installed."""
    return not _is_expected(entry, entry.runtime_data.equipment, device)


def _is_expected(entry: FroelingConfigEntry, equipment: Equipment, device: dr.AnyDeviceEntry) -> bool:
    expected = {(DOMAIN, component.identifier(entry.entry_id)) for component in components(equipment)}
    return bool(device.identifiers & expected)


@callback
def _async_update_child_devices(
    hass: HomeAssistant, entry: FroelingConfigEntry, equipment: Equipment
) -> None:
    """Create or rename the child devices of the equipment and remove those of missing components.

    With Fröling Connect the devices carry the names given there; names set in Home Assistant
    (name_by_user) stay untouched.
    """
    registry = dr.async_get(hass)
    for device in dr.async_child_entries_for_config_entry(registry, entry.entry_id):
        if not _is_expected(entry, equipment, device):
            registry.async_remove_device(device.id)
    for component in components(equipment):
        if component.kind is not ComponentKind.BOILER:
            registry.async_get_or_create_child(
                config_entry_id=entry.entry_id, **child_device_info(entry, component)
            )
