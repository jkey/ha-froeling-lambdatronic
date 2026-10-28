"""Tests for the water heaters of the hot water tanks."""

from dataclasses import replace
from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from froeling_lambdatronic import ConnectionFailedError, hot_water
from homeassistant.components.water_heater import (
    ATTR_OPERATION_MODE,
    ATTR_TEMPERATURE,
    SERVICE_SET_OPERATION_MODE,
    SERVICE_SET_TEMPERATURE,
    WaterHeaterEntityFeature,
)
from homeassistant.components.water_heater import DOMAIN as WATER_HEATER_DOMAIN
from homeassistant.const import (
    ATTR_ENTITY_ID,
    ATTR_SUPPORTED_FEATURES,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    Platform,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    snapshot_platform,
)
from syrupy.assertion import SnapshotAssertion

from . import setup_integration
from .const import FACILITY_ID, VALUES

DHW1 = "water_heater.froling_pe1_brauchwasser"
DHW1_MODBUS = "water_heater.froling_pe1_dhw_tank_01"


@pytest.fixture(autouse=True)
def only_water_heaters() -> object:
    """Load only the water heater platform."""
    with patch("custom_components.froeling_lambdatronic.PLATFORMS", [Platform.WATER_HEATER]):
        yield


@pytest.mark.usefixtures("mock_client", "mock_cloud")
async def test_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    any_config_entry: MockConfigEntry,
) -> None:
    """All water heaters with their registry entries and states for every connection type."""
    await setup_integration(hass, any_config_entry)
    await snapshot_platform(hass, entity_registry, snapshot, any_config_entry.entry_id)


async def test_hybrid(
    hass: HomeAssistant, mock_client: MagicMock, mock_cloud: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Hybrid: the setpoint is written via Modbus, the operating mode via Fröling Connect."""
    await setup_integration(hass, mock_config_entry)
    state = hass.states.get(DHW1)
    assert state is not None
    assert state.state == "auto"

    await hass.services.async_call(
        WATER_HEATER_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: DHW1, ATTR_TEMPERATURE: 52.5},
        blocking=True,
    )
    await hass.services.async_call(
        WATER_HEATER_DOMAIN,
        SERVICE_SET_OPERATION_MODE,
        {ATTR_ENTITY_ID: DHW1, ATTR_OPERATION_MODE: "extra_charge"},
        blocking=True,
    )

    mock_client.write.assert_awaited_once_with(hot_water(1).setpoint, 52.5)
    mock_cloud.set_value.assert_awaited_once_with(FACILITY_ID, "dhw1_mode", 2)


async def test_cloud(
    hass: HomeAssistant, mock_cloud: MagicMock, mock_config_entry_cloud: MockConfigEntry
) -> None:
    """Connect only: the setpoint is written via Fröling Connect."""
    await setup_integration(hass, mock_config_entry_cloud)

    await hass.services.async_call(
        WATER_HEATER_DOMAIN,
        SERVICE_SET_TEMPERATURE,
        {ATTR_ENTITY_ID: DHW1, ATTR_TEMPERATURE: 50},
        blocking=True,
    )

    mock_cloud.set_value.assert_awaited_once_with(FACILITY_ID, "dhw1_setpoint", 50.0)


@pytest.mark.usefixtures("mock_client")
async def test_modbus_without_operation_mode(
    hass: HomeAssistant, mock_config_entry_modbus: MockConfigEntry
) -> None:
    """Modbus only: no operating mode (Modbus has none), temperature and setpoint only."""
    await setup_integration(hass, mock_config_entry_modbus)

    state = hass.states.get(DHW1_MODBUS)
    assert state is not None
    assert state.state == STATE_UNKNOWN
    assert state.attributes[ATTR_SUPPORTED_FEATURES] == WaterHeaterEntityFeature.TARGET_TEMPERATURE
    assert state.attributes["current_temperature"] == 51.0
    assert state.attributes[ATTR_TEMPERATURE] == 55.0


async def test_current_temperature_follows_values(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry_modbus: MockConfigEntry
) -> None:
    """The tank temperature (no sensor of its own) follows the Modbus values, not just the setpoint."""
    await setup_integration(hass, mock_config_entry_modbus)

    mock_client.read_values.return_value = replace(
        VALUES, hot_water={1: replace(VALUES.hot_water[1], temperature=47.5)}
    )
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
    await hass.async_block_till_done()

    state = hass.states.get(DHW1_MODBUS)
    assert state is not None
    assert state.attributes["current_temperature"] == 47.5


async def _fail_cloud(hass: HomeAssistant, mock_cloud: MagicMock, entry: MockConfigEntry) -> None:
    """Let the next Fröling Connect update fail."""
    mock_cloud.get_overview.side_effect = ConnectionFailedError("down")
    cloud = entry.runtime_data.cloud
    assert cloud is not None
    await cloud.async_refresh()
    await hass.async_block_till_done()
    assert not cloud.last_update_success


@pytest.mark.usefixtures("mock_client")
async def test_hybrid_survives_cloud_outage(
    hass: HomeAssistant, mock_cloud: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Hybrid: temperature and setpoint come from Modbus; a Connect outage only hides the mode."""
    await setup_integration(hass, mock_config_entry)

    await _fail_cloud(hass, mock_cloud, mock_config_entry)

    state = hass.states.get(DHW1)
    assert state is not None
    assert state.state == STATE_UNKNOWN
    assert state.attributes["current_temperature"] == 51.0
    assert state.attributes[ATTR_TEMPERATURE] == 55.0
    assert state.attributes[ATTR_OPERATION_MODE] is None


async def test_cloud_unavailable_on_outage(
    hass: HomeAssistant, mock_cloud: MagicMock, mock_config_entry_cloud: MockConfigEntry
) -> None:
    """Connect only: without the cloud the water heater has no source and is unavailable."""
    await setup_integration(hass, mock_config_entry_cloud)

    await _fail_cloud(hass, mock_cloud, mock_config_entry_cloud)

    state = hass.states.get(DHW1)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE
