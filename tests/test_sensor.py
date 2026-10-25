"""Tests for the sensors."""

from dataclasses import replace
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from froeling_lambdatronic import NoResponseError
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from custom_components.froeling_lambdatronic.sensor import FroelingCurveSensor

from . import setup_integration
from .const import PARAMETERS, VALUES


@pytest.fixture(autouse=True)
def only_sensors() -> object:
    """Load only the sensor platform."""
    with patch("custom_components.froeling_lambdatronic.PLATFORMS", [Platform.SENSOR]):
        yield


@pytest.mark.usefixtures("entity_registry_enabled_by_default", "mock_client")
async def test_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    mock_config_entry_local: MockConfigEntry,
) -> None:
    """All sensors with their registry entries and states."""
    await setup_integration(hass, mock_config_entry_local)
    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry_local.entry_id)


@pytest.mark.usefixtures("mock_client")
async def test_disabled_by_default(
    hass: HomeAssistant, entity_registry: er.EntityRegistry, mock_config_entry_local: MockConfigEntry
) -> None:
    """Return and room temperature start disabled."""
    await setup_integration(hass, mock_config_entry_local)
    for entity_id in (
        "sensor.froling_pe1_return_temperature",
        "sensor.froling_pe1_heating_circuit_1_room_temperature",
    ):
        entry = entity_registry.async_get(entity_id)
        assert entry is not None
        assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_fault_sensor(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry_local: MockConfigEntry
) -> None:
    """The first error is the state, further errors are an attribute."""
    mock_client.read_values.return_value = replace(VALUES, errors=(4, 21))
    await setup_integration(hass, mock_config_entry_local)

    state = hass.states.get("sensor.froling_pe1_fault_text")
    assert state is not None
    assert state.state != "Keine Störung"
    assert state.attributes["error_code"] == 4
    assert len(state.attributes["more_errors"]) == 1


async def test_unknown_states(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry_local: MockConfigEntry
) -> None:
    """Unknown state codes and a missing outside temperature become unknown."""
    mock_client.read_values.return_value = replace(
        VALUES, system_state=99, boiler_state=None, outside_temperature=None, pellet_consumption_total=None
    )
    await setup_integration(hass, mock_config_entry_local)

    for entity_id in (
        "sensor.froling_pe1_system_state",
        "sensor.froling_pe1_boiler_state",
        "sensor.froling_pe1_heating_circuit_1_flow_by_heating_curve",
        "sensor.froling_pe1_pellet_consumption",
    ):
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == STATE_UNKNOWN, entity_id


async def test_curve_sensor_follows_parameters(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry_local: MockConfigEntry
) -> None:
    """The curve sensor updates with the parameters and needs them to be available."""
    await setup_integration(hass, mock_config_entry_local)
    entity_id = "sensor.froling_pe1_heating_circuit_1_flow_by_heating_curve"
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "46.4"
    assert state.attributes["curve"][6] == {"outside": 10, "flow": 46.0}
    assert "curve" in FroelingCurveSensor._unrecorded_attributes

    hc1 = replace(PARAMETERS.heating_circuits[1], curve_flow_plus10=50.0)
    mock_client.read_parameters.return_value = replace(
        PARAMETERS, heating_circuits={**PARAMETERS.heating_circuits, 1: hc1}
    )
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=6))
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == "50.3"

    mock_client.read_parameters.side_effect = NoResponseError("timeout")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=12))
    await hass.async_block_till_done()
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE
