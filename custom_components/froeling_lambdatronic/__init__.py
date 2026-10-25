"""The Fröling Lambdatronic integration."""

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
    FroelingConfigEntry,
    FroelingRuntimeData,
    ParametersCoordinator,
    ValuesCoordinator,
)
from .entity import components

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.NUMBER, Platform.SELECT, Platform.SENSOR]


def selected_equipment(entry: FroelingConfigEntry) -> Equipment:
    """Detected equipment reduced to the components the user enabled in the options."""
    detected = entry.data[CONF_EQUIPMENT]

    def pick(key: str) -> tuple[int, ...]:
        chosen = entry.options.get(key, detected[key])
        return tuple(n for n in detected[key] if n in chosen)

    return Equipment(
        heating_circuits=pick(CONF_HEATING_CIRCUITS),
        hot_water=pick(CONF_HOT_WATER),
        buffers=pick(CONF_BUFFERS),
    )


async def async_setup_entry(hass: HomeAssistant, entry: FroelingConfigEntry) -> bool:
    """Set up Fröling Lambdatronic from a config entry."""
    if entry.data.get(CONF_CLOUD) is None:
        _async_remove_cloud_leftovers(hass, entry)
    client = LambdatronicClient(entry.data[CONF_HOST], entry.data[CONF_PORT], entry.data[CONF_SLAVE])
    try:
        await client.connect()
    except FroelingError as err:
        await client.close()
        raise ConfigEntryNotReady(
            translation_domain=DOMAIN,
            translation_key="cannot_connect",
            translation_placeholders={"host": entry.data[CONF_HOST], "error": str(err)},
        ) from err

    equipment = selected_equipment(entry)
    values = ValuesCoordinator(hass, entry, client, equipment)
    parameters = ParametersCoordinator(hass, entry, client, equipment)
    try:
        await values.async_config_entry_first_refresh()
        await parameters.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        await client.close()
        raise

    cloud: CloudCoordinator | None = None
    if (cloud_config := entry.data.get(CONF_CLOUD)) is not None:
        cloud = CloudCoordinator(
            hass,
            entry,
            FroelingConnectClient(
                async_get_clientsession(hass), cloud_config[CONF_USERNAME], cloud_config[CONF_PASSWORD]
            ),
            cloud_config[CONF_FACILITY_ID],
        )
        # The cloud is optional: never block or fail the setup because of it.
        entry.async_create_background_task(hass, cloud.async_refresh(), f"{DOMAIN}_cloud_first_refresh")

    boiler = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer=MANUFACTURER,
        model=MODEL,
    )
    entry.runtime_data = FroelingRuntimeData(client, equipment, boiler.id, values, parameters, cloud)
    _async_remove_stale_devices(hass, entry, equipment)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: FroelingConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.client.close()
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
    """Allow deleting devices of components that are no longer enabled."""
    return not _is_expected(entry, selected_equipment(entry), device)


def _is_expected(entry: FroelingConfigEntry, equipment: Equipment, device: dr.AnyDeviceEntry) -> bool:
    expected = {(DOMAIN, component.identifier(entry.entry_id)) for component in components(equipment)}
    return bool(device.identifiers & expected)


@callback
def _async_remove_stale_devices(
    hass: HomeAssistant, entry: FroelingConfigEntry, equipment: Equipment
) -> None:
    """Remove child devices of components that were disabled in the options."""
    registry = dr.async_get(hass)
    for device in dr.async_child_entries_for_config_entry(registry, entry.entry_id):
        if not _is_expected(entry, equipment, device):
            registry.async_remove_device(device.id)
