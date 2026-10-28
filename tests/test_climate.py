"""Tests for the climate entities of the heating circuits."""

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from froeling_lambdatronic import ConnectionFailedError, heating_circuit
from homeassistant.components.climate import (
    ATTR_CURRENT_TEMPERATURE,
    ATTR_HVAC_ACTION,
    ATTR_HVAC_MODE,
    ATTR_PRESET_MODE,
    ATTR_TEMPERATURE,
    SERVICE_SET_HVAC_MODE,
    SERVICE_SET_PRESET_MODE,
    SERVICE_SET_TEMPERATURE,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, snapshot_platform
from syrupy.assertion import SnapshotAssertion

from custom_components.froeling_lambdatronic.const import CONF_ROOM_SENSORS, DOMAIN

from . import setup_integration
from .const import CLOUD_STATE, ENTRY_ID, ENTRY_OPTIONS, FACILITY_ID, PARAMETERS, UNIQUE_ID, entry_data

HC1 = "climate.froling_pe1_wohnbereich"
HC1_MODBUS = "climate.froling_pe1_heating_circuit_01"


# Monday 26.10.2026, 12:00 in Berlin: inside the heating phase of heating circuit 1 (04:30-22:00).
HEATING_PHASE = "2026-10-26T11:00:00+00:00"
# The same Monday, 22:30 in Berlin: after the phase.
SETBACK_PHASE = "2026-10-26T21:30:00+00:00"


@pytest.fixture(autouse=True)
async def in_heating_phase(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    """Fixed local time inside the heating phase, so the target temperature does not depend on the clock."""
    await hass.config.async_set_time_zone("Europe/Berlin")
    freezer.move_to(HEATING_PHASE)


@pytest.fixture(autouse=True)
def only_climate() -> object:
    """Load only the climate platform."""
    with patch("custom_components.froeling_lambdatronic.PLATFORMS", [Platform.CLIMATE]):
        yield


def _hybrid_entry(room_sensors: list[int] | None) -> MockConfigEntry:
    """Hybrid entry with the given stored room sensors; None: none stored (entries of 0.2.0b1)."""
    data = entry_data()
    if room_sensors is None:
        del data[CONF_ROOM_SENSORS]
    else:
        data[CONF_ROOM_SENSORS] = room_sensors
    return MockConfigEntry(
        domain=DOMAIN,
        version=2,
        entry_id=ENTRY_ID,
        title="Fröling PE1",
        unique_id=UNIQUE_ID,
        data=data,
        options=ENTRY_OPTIONS,
    )


@pytest.fixture
def room_sensor_entry() -> MockConfigEntry:
    """Hybrid entry with a room sensor stored for heating circuit 1."""
    return _hybrid_entry([1])


@pytest.mark.usefixtures("mock_client", "mock_cloud")
async def test_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    any_config_entry: MockConfigEntry,
) -> None:
    """All climate entities with their registry entries and states for every connection type."""
    await setup_integration(hass, any_config_entry)
    await snapshot_platform(hass, entity_registry, snapshot, any_config_entry.entry_id)


def _with_mode(mode: int | None) -> object:
    hc1 = replace(PARAMETERS.heating_circuits[1], operating_mode=mode)
    return replace(PARAMETERS, heating_circuits={**PARAMETERS.heating_circuits, 1: hc1})


@pytest.mark.parametrize(
    ("mode", "hvac_mode", "preset", "target"),
    [
        (0, HVACMode.OFF, None, 20.0),
        (1, HVACMode.AUTO, "automatic", 20.0),
        (2, HVACMode.HEAT, "extra_heating", 20.0),
        (3, HVACMode.AUTO, "setback", 17.0),
        (4, HVACMode.AUTO, "permanent_setback", 17.0),
        (5, HVACMode.AUTO, "party", 20.0),
    ],
)
async def test_modes(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_cloud: MagicMock,
    room_sensor_entry: MockConfigEntry,
    mode: int,
    hvac_mode: HVACMode,
    preset: str | None,
    target: float,
) -> None:
    """Operating mode -> HVAC mode and preset; setting back uses the setback setpoint."""
    mock_client.read_parameters.return_value = _with_mode(mode)
    await setup_integration(hass, room_sensor_entry)

    state = hass.states.get(HC1)
    assert state is not None
    assert state.state == hvac_mode
    assert state.attributes[ATTR_PRESET_MODE] == preset
    assert state.attributes[ATTR_TEMPERATURE] == target
    assert state.attributes[ATTR_CURRENT_TEMPERATURE] == 20.0
    assert state.attributes[ATTR_HVAC_ACTION] == (HVACAction.OFF if mode == 0 else HVACAction.HEATING)


async def test_unknown_mode(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry_modbus: MockConfigEntry
) -> None:
    """An unknown operating mode leaves the state unknown; without Connect there is no HVAC action."""
    mock_client.read_parameters.return_value = _with_mode(None)
    await setup_integration(hass, mock_config_entry_modbus)

    state = hass.states.get(HC1_MODBUS)
    assert state is not None
    assert state.state == STATE_UNKNOWN
    assert state.attributes[ATTR_PRESET_MODE] is None
    assert ATTR_HVAC_ACTION not in state.attributes


async def _call(hass: HomeAssistant, service: str, entity_id: str, **data: object) -> None:
    await hass.services.async_call(
        CLIMATE_DOMAIN, service, {ATTR_ENTITY_ID: entity_id, **data}, blocking=True
    )


async def test_write_modbus(
    hass: HomeAssistant, mock_client: MagicMock, mock_cloud: MagicMock, room_sensor_entry: MockConfigEntry
) -> None:
    """Hybrid: modes and the room setpoint are written via Modbus."""
    await setup_integration(hass, room_sensor_entry)

    await _call(hass, SERVICE_SET_HVAC_MODE, HC1, **{ATTR_HVAC_MODE: HVACMode.HEAT})
    await _call(hass, SERVICE_SET_PRESET_MODE, HC1, **{ATTR_PRESET_MODE: "party"})
    await _call(hass, SERVICE_TURN_OFF, HC1)
    await _call(hass, SERVICE_TURN_ON, HC1)
    await _call(hass, SERVICE_SET_TEMPERATURE, HC1, **{ATTR_TEMPERATURE: 21.5})

    registers = heating_circuit(1)
    assert [call.args for call in mock_client.write.await_args_list] == [
        (registers.operating_mode, 2.0),
        (registers.operating_mode, 5.0),
        (registers.operating_mode, 0.0),
        (registers.operating_mode, 1.0),
        (registers.room_setpoint_heating, 21.5),
    ]
    mock_cloud.set_value.assert_not_awaited()


@pytest.mark.parametrize(("pump", "action"), [(True, HVACAction.HEATING), (False, HVACAction.IDLE)])
async def test_cloud(
    hass: HomeAssistant,
    mock_cloud: MagicMock,
    mock_config_entry_cloud: MockConfigEntry,
    pump: bool,
    action: HVACAction,
) -> None:
    """Connect only: flow temperature, no target temperature, action from the pump, modes via the cloud."""
    mock_cloud.snapshot_changes = {"hc1_pump": pump}
    await setup_integration(hass, mock_config_entry_cloud)

    state = hass.states.get(HC1)
    assert state is not None
    assert state.attributes[ATTR_CURRENT_TEMPERATURE] == 37.0
    assert ATTR_TEMPERATURE not in state.attributes
    assert not state.attributes[ATTR_SUPPORTED_FEATURES] & ClimateEntityFeature.TARGET_TEMPERATURE
    assert state.attributes[ATTR_HVAC_ACTION] == action

    await _call(hass, SERVICE_SET_PRESET_MODE, HC1, **{ATTR_PRESET_MODE: "setback"})

    mock_cloud.set_value.assert_awaited_once_with(FACILITY_ID, "hc1_operating_mode", 3)


async def test_without_room_sensor(
    hass: HomeAssistant, mock_client: MagicMock, mock_cloud: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """No room sensor stored: the placeholder 20.0 °C of Modbus is ignored, no target temperature."""
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(HC1)
    assert state is not None
    assert state.attributes[ATTR_CURRENT_TEMPERATURE] == 40.5
    assert ATTR_TEMPERATURE not in state.attributes
    assert not state.attributes[ATTR_SUPPORTED_FEATURES] & ClimateEntityFeature.TARGET_TEMPERATURE


@pytest.mark.usefixtures("mock_client", "mock_cloud")
async def test_room_sensor(hass: HomeAssistant, room_sensor_entry: MockConfigEntry) -> None:
    """Room sensor stored for heating circuit 1: room temperature and room setpoint (Modbus limits)."""
    await setup_integration(hass, room_sensor_entry)

    state = hass.states.get(HC1)
    assert state is not None
    assert state.attributes[ATTR_CURRENT_TEMPERATURE] == 20.0
    assert state.attributes[ATTR_TEMPERATURE] == 20.0
    assert state.attributes[ATTR_SUPPORTED_FEATURES] & ClimateEntityFeature.TARGET_TEMPERATURE
    # Heating circuit 2 has no room sensor.
    state = hass.states.get("climate.froling_pe1_anbau")
    assert state is not None
    assert state.attributes[ATTR_CURRENT_TEMPERATURE] == 32.0
    assert ATTR_TEMPERATURE not in state.attributes


@pytest.mark.usefixtures("mock_client")
async def test_modbus_without_room_sensor(
    hass: HomeAssistant, mock_config_entry_modbus: MockConfigEntry
) -> None:
    """Modbus only cannot tell a room sensor from the placeholder 20.0 °C: flow temperature, no setpoint."""
    await setup_integration(hass, mock_config_entry_modbus)

    state = hass.states.get(HC1_MODBUS)
    assert state is not None
    assert state.attributes[ATTR_CURRENT_TEMPERATURE] == 40.5
    assert not state.attributes[ATTR_SUPPORTED_FEATURES] & ClimateEntityFeature.TARGET_TEMPERATURE


@pytest.mark.parametrize(("stored", "target"), [([1], True), ([], False), (None, False)])
async def test_room_sensor_stored_while_connect_down(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_cloud: MagicMock,
    stored: list[int] | None,
    target: bool,
) -> None:
    """Connect unreachable at setup: the stored room sensors apply; none stored (0.2.0b1) means none."""
    mock_cloud.get_components.side_effect = ConnectionFailedError("down")
    entry = _hybrid_entry(stored)
    mock_cloud.get_overview.side_effect = ConnectionFailedError("down")
    await setup_integration(hass, entry)

    state = hass.states.get(HC1_MODBUS)
    assert state is not None
    assert bool(state.attributes[ATTR_SUPPORTED_FEATURES] & ClimateEntityFeature.TARGET_TEMPERATURE) is target
    assert state.attributes[ATTR_CURRENT_TEMPERATURE] == (20.0 if target else 40.5)
    assert entry.data.get(CONF_ROOM_SENSORS) == stored


async def test_unavailable_without_remote_control(
    hass: HomeAssistant, mock_cloud: MagicMock, mock_config_entry_cloud: MockConfigEntry
) -> None:
    """Connect only: a heating circuit cannot be switched while remote control is off."""
    mock_cloud.get_boiler_state.return_value = replace(CLOUD_STATE, remote_control_allowed=False)
    await setup_integration(hass, mock_config_entry_cloud)

    state = hass.states.get(HC1)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE


@pytest.mark.parametrize(("moment", "target"), [(HEATING_PHASE, 20.0), (SETBACK_PHASE, 17.0)])
async def test_automatic_follows_schedule(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_client: MagicMock,
    mock_cloud: MagicMock,
    room_sensor_entry: MockConfigEntry,
    moment: str,
    target: float,
) -> None:
    """Automatic with a known schedule: heating setpoint inside a phase, setback setpoint outside."""
    freezer.move_to(moment)
    await setup_integration(hass, room_sensor_entry)

    state = hass.states.get(HC1)
    assert state is not None
    assert state.attributes[ATTR_TEMPERATURE] == target

    await _call(hass, SERVICE_SET_TEMPERATURE, HC1, **{ATTR_TEMPERATURE: 18.0})
    registers = heating_circuit(1)
    expected = registers.room_setpoint_heating if target == 20.0 else registers.room_setpoint_setback
    mock_client.write.assert_awaited_once_with(expected, 18.0)


async def test_automatic_without_schedule(
    hass: HomeAssistant,
    freezer: FrozenDateTimeFactory,
    mock_client: MagicMock,
    mock_cloud: MagicMock,
) -> None:
    """Without a schedule from Connect (unreachable) automatic shows the heating setpoint."""
    freezer.move_to(SETBACK_PHASE)
    entry = _hybrid_entry([1])
    mock_cloud.get_overview.side_effect = ConnectionFailedError("down")
    await setup_integration(hass, entry)

    state = hass.states.get(HC1_MODBUS)
    assert state is not None
    assert state.attributes[ATTR_TEMPERATURE] == 20.0
