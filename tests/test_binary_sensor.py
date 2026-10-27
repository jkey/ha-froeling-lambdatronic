"""Tests for the binary sensors."""

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.const import STATE_ON, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, snapshot_platform
from syrupy.assertion import SnapshotAssertion

from custom_components.froeling_lambdatronic.const import ConnectionType

from . import setup_integration
from .const import VALUES


@pytest.fixture(autouse=True)
def only_binary_sensors() -> object:
    """Load only the binary sensor platform."""
    with patch("custom_components.froeling_lambdatronic.PLATFORMS", [Platform.BINARY_SENSOR]):
        yield


@pytest.mark.parametrize("any_config_entry", [ConnectionType.MODBUS, ConnectionType.HYBRID], indirect=True)
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
