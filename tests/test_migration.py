"""Tests for the migration of 0.1.0 entries (version 1)."""

from unittest.mock import MagicMock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.froeling_lambdatronic.const import (
    CONF_BUFFERS,
    CONF_CLOUD,
    CONF_CLOUD_INTERVAL,
    CONF_EQUIPMENT,
    CONF_FACILITY_ID,
    CONF_HEATING_CIRCUITS,
    CONF_HOT_WATER,
    CONF_PARAMETER_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_SLAVE,
    DOMAIN,
)

from . import setup_integration
from .const import ENTRY_ID, FACILITY_ID, HOST, PASSWORD, UNIQUE_ID, USERNAME

V1_EQUIPMENT = {CONF_HEATING_CIRCUITS: [1, 2], CONF_HOT_WATER: [1], CONF_BUFFERS: [1]}


def _v1_entry(*, cloud: bool, selection: dict[str, list[int]] | None = None) -> MockConfigEntry:
    data: dict[str, object] = {CONF_HOST: HOST, CONF_PORT: 502, CONF_SLAVE: 2, CONF_EQUIPMENT: V1_EQUIPMENT}
    if cloud:
        data[CONF_CLOUD] = {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD, CONF_FACILITY_ID: FACILITY_ID}
    return MockConfigEntry(
        domain=DOMAIN,
        version=1,
        entry_id=ENTRY_ID,
        title="Fröling PE1",
        unique_id=UNIQUE_ID,
        data=data,
        options={**(selection or {}), CONF_SCAN_INTERVAL: 30, CONF_PARAMETER_INTERVAL: 5},
    )


@pytest.mark.usefixtures("mock_client", "mock_cloud")
async def test_migrate_v1(
    hass: HomeAssistant, device_registry: dr.DeviceRegistry, entity_registry: er.EntityRegistry
) -> None:
    """Deselected components become disabled devices; replaced entities go, the control output is renamed."""
    entry = _v1_entry(
        cloud=True, selection={CONF_HEATING_CIRCUITS: [1], CONF_HOT_WATER: [1], CONF_BUFFERS: []}
    )
    entry.add_to_hass(hass)
    old = {
        (platform, key): entity_registry.async_get_or_create(
            platform, DOMAIN, f"{ENTRY_ID}_{key}", config_entry=entry
        )
        for platform, key in (
            ("select", "hc1_operating_mode"),
            ("select", "system_operating_mode"),
            ("number", "dhw1_setpoint"),
            ("number", "boiler_setpoint"),
            ("sensor", "pellet_counter_kg"),
            ("sensor", "boiler_output"),
        )
    }

    await setup_integration(hass, entry)

    assert entry.state is ConfigEntryState.LOADED
    assert entry.version == 2
    assert entry.options == {CONF_SCAN_INTERVAL: 30, CONF_PARAMETER_INTERVAL: 5, CONF_CLOUD_INTERVAL: 2}
    assert entry.data[CONF_EQUIPMENT] == {**V1_EQUIPMENT, "circulation_pump": False}
    for key in ("hc2", "buffer1"):
        device = device_registry.async_get_child_device_by_identifier((DOMAIN, f"{ENTRY_ID}_{key}"), ENTRY_ID)
        assert device is not None
        assert device.disabled_by is dr.DeviceEntryDisabler.USER
    hc1 = device_registry.async_get_child_device_by_identifier((DOMAIN, f"{ENTRY_ID}_hc1"), ENTRY_ID)
    assert hc1 is not None
    assert hc1.disabled_by is None
    hc2_flow = entity_registry.async_get_entity_id("sensor", DOMAIN, f"{ENTRY_ID}_hc2_flow_temperature")
    assert hc2_flow is not None
    hc2_entry = entity_registry.async_get(hc2_flow)
    assert hc2_entry is not None
    assert hc2_entry.disabled_by is er.RegistryEntryDisabler.DEVICE

    for (_, key), entity in old.items():
        current = entity_registry.async_get(entity.entity_id)
        removed = key in ("hc1_operating_mode", "dhw1_setpoint", "pellet_counter_kg")
        assert (current is None) is removed, key
    control = entity_registry.async_get(old[("sensor", "boiler_output")].entity_id)
    assert control is not None
    assert control.unique_id == f"{ENTRY_ID}_boiler_control_output"


@pytest.mark.usefixtures("mock_client")
async def test_migrate_v1_without_cloud_and_selection(
    hass: HomeAssistant, device_registry: dr.DeviceRegistry
) -> None:
    """Without a selection nothing is disabled; without Connect no cloud interval is added."""
    entry = _v1_entry(cloud=False)
    await setup_integration(hass, entry)

    assert entry.version == 2
    assert entry.options == {CONF_SCAN_INTERVAL: 30, CONF_PARAMETER_INTERVAL: 5}
    for device in dr.async_child_entries_for_config_entry(device_registry, ENTRY_ID):
        assert device.disabled_by is None


async def test_migrate_from_future_version(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """A newer entry version cannot be migrated back."""
    entry = _v1_entry(cloud=False)
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(entry, version=3)

    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.MIGRATION_ERROR


@pytest.mark.usefixtures("mock_client", "mock_cloud")
async def test_migrate_v1_restores_deleted_device_disabled(
    hass: HomeAssistant, device_registry: dr.DeviceRegistry, entity_registry: er.EntityRegistry
) -> None:
    """A device deleted by deselecting it in 0.1.0 comes back disabled by the user, with its old entity."""
    entry = _v1_entry(cloud=True, selection={CONF_HEATING_CIRCUITS: [1]})
    entry.add_to_hass(hass)
    boiler = device_registry.async_get_or_create(
        config_entry_id=ENTRY_ID, identifiers={(DOMAIN, ENTRY_ID)}, name="Fröling PE1"
    )
    hc2 = device_registry.async_get_or_create_child(
        config_entry_id=ENTRY_ID, identifiers={(DOMAIN, f"{ENTRY_ID}_hc2")}, parent_device_id=boiler.id
    )
    old = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{ENTRY_ID}_hc2_flow_temperature",
        config_entry=entry,
        device_id=hc2.id,
        suggested_object_id="old_hc2_flow",
    )
    # 0.1.0 deleted the device of a deselected component.
    device_registry.async_remove_device(hc2.id)
    assert entity_registry.async_get(old.entity_id) is None

    await setup_integration(hass, entry)

    device = device_registry.async_get_child_device_by_identifier((DOMAIN, f"{ENTRY_ID}_hc2"), ENTRY_ID)
    assert device is not None
    assert device.disabled_by is dr.DeviceEntryDisabler.USER
    restored = entity_registry.async_get_entity_id("sensor", DOMAIN, f"{ENTRY_ID}_hc2_flow_temperature")
    assert restored == old.entity_id
