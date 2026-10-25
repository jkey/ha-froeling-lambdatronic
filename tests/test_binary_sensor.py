"""Tests for the binary sensors."""

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.const import STATE_ON, Platform
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


@pytest.mark.usefixtures("mock_client")
async def test_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    mock_config_entry_local: MockConfigEntry,
) -> None:
    """All binary sensors with their registry entries and states."""
    await setup_integration(hass, mock_config_entry_local)
    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry_local.entry_id)


async def test_fault_from_boiler_state(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry_local: MockConfigEntry
) -> None:
    """A fault boiler state turns the problem sensor on even with an empty error buffer."""
    mock_client.read_values.return_value = replace(VALUES, boiler_state=0, boiler_demand=True)
    await setup_integration(hass, mock_config_entry_local)

    for entity_id in ("binary_sensor.froling_pe1_fault", "binary_sensor.froling_pe1_boiler_demand"):
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == STATE_ON
