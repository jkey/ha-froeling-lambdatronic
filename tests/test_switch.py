"""Tests for the switches."""

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest
from froeling_lambdatronic import BOILER
from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
    STATE_UNKNOWN,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, snapshot_platform
from syrupy.assertion import SnapshotAssertion

from . import setup_integration
from .const import FACILITY_ID, PARAMETERS

BOILER_ON = "switch.froling_pe1_boiler"
AUTO_FEEDER = "switch.froling_pe1_automatic_feed"


@pytest.fixture(autouse=True)
def only_switches() -> object:
    """Load only the switch platform."""
    with patch("custom_components.froeling_lambdatronic.PLATFORMS", [Platform.SWITCH]):
        yield


@pytest.mark.usefixtures("mock_client", "mock_cloud")
async def test_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    any_config_entry: MockConfigEntry,
) -> None:
    """All switches with their registry entries and states for every connection type."""
    await setup_integration(hass, any_config_entry)
    await snapshot_platform(hass, entity_registry, snapshot, any_config_entry.entry_id)


async def _switch(hass: HomeAssistant, service: str, entity_id: str) -> None:
    await hass.services.async_call(SWITCH_DOMAIN, service, {ATTR_ENTITY_ID: entity_id}, blocking=True)


async def test_switch_hybrid(
    hass: HomeAssistant, mock_client: MagicMock, mock_cloud: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The automatic feed is written inverted via Modbus, the boiler switch via Fröling Connect."""
    await setup_integration(hass, mock_config_entry)

    await _switch(hass, SERVICE_TURN_OFF, AUTO_FEEDER)
    await _switch(hass, SERVICE_TURN_ON, AUTO_FEEDER)
    await _switch(hass, SERVICE_TURN_OFF, BOILER_ON)
    await _switch(hass, SERVICE_TURN_ON, BOILER_ON)

    assert [call.args for call in mock_client.write.await_args_list] == [
        (BOILER.auto_feeder_off, 1.0),
        (BOILER.auto_feeder_off, 0.0),
    ]
    assert [call.args for call in mock_cloud.set_value.await_args_list] == [
        (FACILITY_ID, "boiler_on", False),
        (FACILITY_ID, "boiler_on", True),
    ]


async def test_switch_cloud(
    hass: HomeAssistant, mock_cloud: MagicMock, mock_config_entry_cloud: MockConfigEntry
) -> None:
    """Connect only: the automatic feed is written inverted via Fröling Connect."""
    await setup_integration(hass, mock_config_entry_cloud)
    state = hass.states.get(AUTO_FEEDER)
    assert state is not None
    assert state.state == STATE_ON

    await _switch(hass, SERVICE_TURN_OFF, AUTO_FEEDER)

    mock_cloud.set_value.assert_awaited_once_with(FACILITY_ID, "auto_feeder_off", True)


async def test_switch_modbus(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry_modbus: MockConfigEntry
) -> None:
    """Modbus only: no boiler switch; an unknown feed setting is unknown."""
    mock_client.read_parameters.return_value = replace(PARAMETERS, auto_feeder_off=None)
    await setup_integration(hass, mock_config_entry_modbus)

    assert hass.states.get(BOILER_ON) is None
    state = hass.states.get(AUTO_FEEDER)
    assert state is not None
    assert state.state == STATE_UNKNOWN
    assert state.state != STATE_OFF
