"""The Fröling Lambdatronic integration."""

from collections.abc import Mapping
from typing import Any

from froeling_lambdatronic import Equipment, FroelingConnectClient, FroelingError, LambdatronicClient
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_BUFFERS,
    CONF_CIRCULATION_PUMP,
    CONF_CLOUD,
    CONF_EQUIPMENT,
    CONF_FACILITY_ID,
    CONF_HEATING_CIRCUITS,
    CONF_HOT_WATER,
    CONF_SLAVE,
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
)
from .entity import ComponentKind, child_device_info, components
from .sources import FroelingSources

PLATFORMS = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.TIME,
]


def equipment_from_data(data: Mapping[str, Any]) -> Equipment:
    """The equipment stored in the entry data."""
    return Equipment(
        heating_circuits=tuple(data[CONF_HEATING_CIRCUITS]),
        hot_water=tuple(data[CONF_HOT_WATER]),
        buffers=tuple(data[CONF_BUFFERS]),
        circulation_pump=data[CONF_CIRCULATION_PUMP],
    )


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
