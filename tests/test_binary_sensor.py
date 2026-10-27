"""Tests for the binary sensors."""

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest
from froeling_lambdatronic import CloudHeader
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNKNOWN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, snapshot_platform
from syrupy.assertion import SnapshotAssertion

from . import setup_integration
from .const import VALUES


@pytest.fixture(autouse=True)
def only_binary_sensors() -> object:
    """Load only the binary sensor platform."""
    with patch("custom_components.froeling_lambdatronic.PLATFORMS", [Platform.BINARY_SENSOR]):
        yield


@pytest.mark.usefixtures("mock_client", "mock_cloud")
async def test_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    any_config_entry: MockConfigEntry,
) -> None:
    """All binary sensors with their registry entries and states. for every connection type."""
    await setup_integration(hass, any_config_entry)
    await snapshot_platform(hass, entity_registry, snapshot, any_config_entry.entry_id)


async def test_fault_from_boiler_state(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry_modbus: MockConfigEntry
) -> None:
    """A fault boiler state turns the problem sensor on even with an empty error buffer."""
    mock_client.read_values.return_value = replace(VALUES, boiler_state=0, boiler_demand=True)
    await setup_integration(hass, mock_config_entry_modbus)

    for entity_id in ("binary_sensor.froling_pe1_fault", "binary_sensor.froling_pe1_boiler_demand"):
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == STATE_ON


async def test_fault_from_cloud_state(
    hass: HomeAssistant, mock_cloud: MagicMock, mock_config_entry_cloud: MockConfigEntry
) -> None:
    """Connect only: the boiler state of the cloud decides; the history alone does not."""
    await setup_integration(hass, mock_config_entry_cloud)
    state = hass.states.get("binary_sensor.froling_pe1_fault")
    assert state is not None
    assert state.state == STATE_OFF

    mock_cloud.snapshot_changes = {"boiler_state": 0}
    await mock_config_entry_cloud.runtime_data.cloud.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.froling_pe1_fault")
    assert state is not None
    assert state.state == STATE_ON

    mock_cloud.snapshot_changes = {"boiler_state": None}
    await mock_config_entry_cloud.runtime_data.cloud.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get("binary_sensor.froling_pe1_fault")
    assert state is not None
    assert state.state == STATE_UNKNOWN


async def test_connect_offline(
    hass: HomeAssistant, mock_client: MagicMock, mock_cloud: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The connectivity sensor follows the connection state reported by Fröling Connect."""
    mock_cloud.get_header.return_value = CloudHeader(connect_state="OFFLINE", last_signal=None)
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get("binary_sensor.froling_pe1_froling_connect")
    assert state is not None
    assert state.state == STATE_OFF
