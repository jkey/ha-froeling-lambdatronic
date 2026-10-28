"""Tests for the sensors."""

from dataclasses import replace
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from froeling_lambdatronic import CirculationValues, CloudError, ConnectionFailedError, NoResponseError
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

from custom_components.froeling_lambdatronic.const import DOMAIN
from custom_components.froeling_lambdatronic.sensor import FroelingCurveSensor, FroelingFaultSensor

from . import setup_integration
from .const import CLOUD_ERRORS, PARAMETERS, VALUES

FAULT_TEXT = "sensor.froling_pe1_fault_text"
CIRCULATION_RETURN = "sensor.froling_pe1_circulation_pump_return_temperature"


@pytest.fixture(autouse=True)
def only_sensors() -> object:
    """Load only the sensor platform."""
    with patch("custom_components.froeling_lambdatronic.PLATFORMS", [Platform.SENSOR]):
        yield


@pytest.mark.usefixtures("entity_registry_enabled_by_default", "mock_client", "mock_cloud")
async def test_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    any_config_entry: MockConfigEntry,
) -> None:
    """All sensors with their registry entries and states. for every connection type."""
    await setup_integration(hass, any_config_entry)
    await snapshot_platform(hass, entity_registry, snapshot, any_config_entry.entry_id)


@pytest.mark.usefixtures("mock_client", "mock_cloud")
async def test_disabled_by_default(
    hass: HomeAssistant, entity_registry: er.EntityRegistry, mock_config_entry: MockConfigEntry
) -> None:
    """Return and room temperature, ignition limit and connection times start disabled."""
    await setup_integration(hass, mock_config_entry)
    for entity_id in (
        "sensor.froling_pe1_return_temperature",
        "sensor.froling_pe1_wohnbereich_room_temperature",
        "sensor.froling_pe1_pufferspeicher_ignition_below_buffer_temperature",
        "sensor.froling_pe1_connected_since",
        "sensor.froling_pe1_last_signal",
    ):
        entry = entity_registry.async_get(entity_id)
        assert entry is not None
        assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_fault_sensor(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry_modbus: MockConfigEntry
) -> None:
    """The first error is the state, further errors are an attribute."""
    mock_client.read_values.return_value = replace(VALUES, errors=(4, 21))
    await setup_integration(hass, mock_config_entry_modbus)

    state = hass.states.get(FAULT_TEXT)
    assert state is not None
    assert state.state != "Keine Störung"
    assert state.attributes["error_code"] == 4
    assert len(state.attributes["more_errors"]) == 1
    assert "history" not in state.attributes


async def test_fault_sensor_hybrid(
    hass: HomeAssistant, mock_client: MagicMock, mock_cloud: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Hybrid: text from Modbus; time of the newest cloud message while a fault is active."""
    mock_client.read_values.return_value = replace(VALUES, errors=(4,))
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(FAULT_TEXT)
    assert state is not None
    assert state.attributes["error_code"] == 4
    assert state.attributes["since"] == "2026-10-21T17:40:12.345000+00:00"
    assert [entry["message"] for entry in state.attributes["history"]] == [
        "Pelletlager Mindestbestand erreicht",
        "Kessel Übertemperatur",
    ]
    assert {"since", "history"} <= FroelingFaultSensor._unrecorded_attributes

    mock_cloud.get_errors.side_effect = ConnectionFailedError("down")
    await mock_config_entry.runtime_data.cloud.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get(FAULT_TEXT)
    assert state is not None
    assert state.state != STATE_UNAVAILABLE


async def test_fault_sensor_cloud(
    hass: HomeAssistant, mock_cloud: MagicMock, mock_config_entry_cloud: MockConfigEntry
) -> None:
    """Connect only: newest message while the boiler is in a fault state, else the state text."""
    undated = CloudError(
        error_id=None, timestamp=None, severity="alarm", message="Ohne Zeit", resolutions=None
    )
    mock_cloud.get_errors.return_value = [undated, *CLOUD_ERRORS]
    mock_cloud.snapshot_changes = {"boiler_state": 41}
    await setup_integration(hass, mock_config_entry_cloud)

    state = hass.states.get(FAULT_TEXT)
    assert state is not None
    assert state.state == "Pelletlager Mindestbestand erreicht"
    assert state.attributes["since"] == "2026-10-21T17:40:12.345000+00:00"
    assert [entry["message"] for entry in state.attributes["history"]][-1] == "Ohne Zeit"
    assert "error_code" not in state.attributes

    mock_cloud.get_errors.return_value = []
    await mock_config_entry_cloud.runtime_data.cloud.async_refresh()
    await hass.async_block_till_done()
    state = hass.states.get(FAULT_TEXT)
    assert state is not None
    assert state.state == "STÖRUNG: STB/NA"
    assert state.attributes["since"] is None


async def test_circulation_return_temperature(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry_modbus: MockConfigEntry
) -> None:
    """The return temperature of the circulation pump exists only with a configured probe."""
    await setup_integration(hass, mock_config_entry_modbus)
    assert hass.states.get(CIRCULATION_RETURN) is None
    await hass.config_entries.async_unload(mock_config_entry_modbus.entry_id)

    mock_client.read_values.return_value = replace(
        VALUES, circulation=CirculationValues(flow_switch=False, pump=0.0, return_temperature=24.5)
    )
    await hass.config_entries.async_reload(mock_config_entry_modbus.entry_id)
    await hass.async_block_till_done()
    state = hass.states.get(CIRCULATION_RETURN)
    assert state is not None
    assert state.state == "24.5"


@pytest.mark.usefixtures("mock_cloud")
async def test_circulation_return_temperature_cloud(
    hass: HomeAssistant, entity_registry: er.EntityRegistry, mock_config_entry_cloud: MockConfigEntry
) -> None:
    """Connect only: the return temperature exists but starts disabled (0 without a probe)."""
    await setup_integration(hass, mock_config_entry_cloud)

    entity_id = entity_registry.async_get_entity_id(
        "sensor", DOMAIN, f"{mock_config_entry_cloud.entry_id}_circulation_return_temperature"
    )
    assert entity_id is not None
    entry = entity_registry.async_get(entity_id)
    assert entry is not None
    assert entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION


@pytest.mark.usefixtures("entity_registry_enabled_by_default", "mock_client", "mock_cloud")
async def test_no_duplicate_sensors(
    hass: HomeAssistant, entity_registry: er.EntityRegistry, any_config_entry: MockConfigEntry
) -> None:
    """Tank temperature and system state have no sensor: water heater and select show them."""
    await setup_integration(hass, any_config_entry)
    for key in ("dhw1_temperature", "system_state"):
        unique_id = f"{any_config_entry.entry_id}_{key}"
        assert entity_registry.async_get_entity_id("sensor", DOMAIN, unique_id) is None, key


async def test_unknown_states(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry_modbus: MockConfigEntry
) -> None:
    """Unknown state codes and a missing outside temperature become unknown."""
    mock_client.read_values.return_value = replace(
        VALUES, system_state=99, boiler_state=None, outside_temperature=None, pellet_consumption_total=None
    )
    await setup_integration(hass, mock_config_entry_modbus)

    for entity_id in (
        "sensor.froling_pe1_boiler_state",
        "sensor.froling_pe1_heating_circuit_01_flow_by_heating_curve",
        "sensor.froling_pe1_pellet_consumption",
    ):
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == STATE_UNKNOWN, entity_id


async def test_curve_sensor_follows_parameters(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry_modbus: MockConfigEntry
) -> None:
    """The curve sensor updates with the parameters and needs them to be available."""
    await setup_integration(hass, mock_config_entry_modbus)
    entity_id = "sensor.froling_pe1_heating_circuit_01_flow_by_heating_curve"
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
