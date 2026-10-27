"""Tests for the climate entities of the heating circuits."""

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from froeling_lambdatronic import HeatingCircuitValues, heating_circuit
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

from . import setup_integration
from .const import CLOUD_STATE, FACILITY_ID, PARAMETERS, VALUES

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
    mock_config_entry: MockConfigEntry,
    mode: int,
    hvac_mode: HVACMode,
    preset: str | None,
    target: float,
) -> None:
    """Operating mode -> HVAC mode and preset; setting back uses the setback setpoint."""
    mock_client.read_parameters.return_value = _with_mode(mode)
    await setup_integration(hass, mock_config_entry)

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
    hass: HomeAssistant, mock_client: MagicMock, mock_cloud: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Hybrid: modes and the room setpoint are written via Modbus."""
    await setup_integration(hass, mock_config_entry)

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


async def test_cloud(
    hass: HomeAssistant, mock_cloud: MagicMock, mock_config_entry_cloud: MockConfigEntry
) -> None:
    """Connect only: flow temperature, no target temperature, modes written via the cloud."""
    mock_cloud.snapshot_changes = {"hc1_pump": False}
    await setup_integration(hass, mock_config_entry_cloud)

    state = hass.states.get(HC1)
    assert state is not None
    assert state.attributes[ATTR_CURRENT_TEMPERATURE] == 37.0
    assert ATTR_TEMPERATURE not in state.attributes or state.attributes[ATTR_TEMPERATURE] is None
    assert not state.attributes[ATTR_SUPPORTED_FEATURES] & ClimateEntityFeature.TARGET_TEMPERATURE
    assert state.attributes[ATTR_HVAC_ACTION] == HVACAction.IDLE

    await _call(hass, SERVICE_SET_PRESET_MODE, HC1, **{ATTR_PRESET_MODE: "setback"})

    mock_cloud.set_value.assert_awaited_once_with(FACILITY_ID, "hc1_operating_mode", 3)


async def test_without_room_sensor(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry_modbus: MockConfigEntry
) -> None:
    """Without a room sensor the flow temperature is shown and no target temperature offered."""
    mock_client.read_values.return_value = replace(
        VALUES,
        heating_circuits={
            **VALUES.heating_circuits,
            1: HeatingCircuitValues(flow_temperature=40.5, flow_setpoint=0.0, room_temperature=None),
        },
    )
    await setup_integration(hass, mock_config_entry_modbus)

    state = hass.states.get(HC1_MODBUS)
    assert state is not None
    assert state.attributes[ATTR_CURRENT_TEMPERATURE] == 40.5
    assert not state.attributes[ATTR_SUPPORTED_FEATURES] & ClimateEntityFeature.TARGET_TEMPERATURE


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
    mock_config_entry: MockConfigEntry,
    moment: str,
    target: float,
) -> None:
    """Automatic with a known schedule: heating setpoint inside a phase, setback setpoint outside."""
    freezer.move_to(moment)
    await setup_integration(hass, mock_config_entry)

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
    mock_config_entry_modbus: MockConfigEntry,
) -> None:
    """Without Fröling Connect the schedule is unknown: automatic shows the heating setpoint."""
    freezer.move_to(SETBACK_PHASE)
    await setup_integration(hass, mock_config_entry_modbus)

    state = hass.states.get(HC1_MODBUS)
    assert state is not None
    assert state.attributes[ATTR_TEMPERATURE] == 20.0
