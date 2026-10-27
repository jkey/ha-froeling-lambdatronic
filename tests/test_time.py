"""Tests for the time entities."""

from dataclasses import replace
from datetime import time
from unittest.mock import MagicMock, patch

import pytest
from froeling_lambdatronic import BOILER
from homeassistant.components.time import ATTR_TIME, SERVICE_SET_VALUE
from homeassistant.components.time import DOMAIN as TIME_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE, STATE_UNKNOWN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, snapshot_platform
from syrupy.assertion import SnapshotAssertion

from . import setup_integration
from .const import CLOUD_STATE, FACILITY_ID, PARAMETERS

FILLING_1 = "time.froling_pe1_1st_pellet_filling"
FILLING_2 = "time.froling_pe1_2nd_pellet_filling"


@pytest.fixture(autouse=True)
def only_times() -> object:
    """Load only the time platform."""
    with patch("custom_components.froeling_lambdatronic.PLATFORMS", [Platform.TIME]):
        yield


@pytest.mark.usefixtures("mock_client", "mock_cloud")
async def test_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    any_config_entry: MockConfigEntry,
) -> None:
    """All time entities with their registry entries and states for every connection type."""
    await setup_integration(hass, any_config_entry)
    await snapshot_platform(hass, entity_registry, snapshot, any_config_entry.entry_id)


async def _set(hass: HomeAssistant, entity_id: str, value: time) -> None:
    await hass.services.async_call(
        TIME_DOMAIN, SERVICE_SET_VALUE, {ATTR_ENTITY_ID: entity_id, ATTR_TIME: value}, blocking=True
    )


async def test_write_hybrid(
    hass: HomeAssistant, mock_client: MagicMock, mock_cloud: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The first filling is written via Modbus (minutes), the second, read-only there, via Connect."""
    await setup_integration(hass, mock_config_entry)
    parameter_reads = mock_client.read_parameters.await_count
    cloud_reads = mock_cloud.get_overview.await_count

    await _set(hass, FILLING_2, time(21, 15))

    mock_cloud.set_value.assert_awaited_once_with(FACILITY_ID, "pellet_filling_start_2", time(21, 15))
    # Read via Modbus, written via the cloud: both are read again.
    assert mock_cloud.get_overview.await_count == cloud_reads + 1
    assert mock_client.read_parameters.await_count == parameter_reads + 1

    await _set(hass, FILLING_1, time(9, 30))

    mock_client.write.assert_awaited_once_with(BOILER.pellet_filling_start_1, 570)


async def test_modbus_only(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry_modbus: MockConfigEntry
) -> None:
    """Modbus only: the read-only second filling has no entity; an unknown time is unknown."""
    mock_client.read_parameters.return_value = replace(PARAMETERS, pellet_filling_start_1=None)
    await setup_integration(hass, mock_config_entry_modbus)

    assert hass.states.get(FILLING_2) is None
    state = hass.states.get(FILLING_1)
    assert state is not None
    assert state.state == STATE_UNKNOWN


async def test_unavailable_without_remote_control(
    hass: HomeAssistant, mock_client: MagicMock, mock_cloud: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Written via the cloud: unavailable while remote control is off, the Modbus one stays."""
    mock_cloud.get_boiler_state.return_value = replace(CLOUD_STATE, remote_control_allowed=False)
    await setup_integration(hass, mock_config_entry)

    for entity_id, unavailable in ((FILLING_1, False), (FILLING_2, True)):
        state = hass.states.get(entity_id)
        assert state is not None
        assert (state.state == STATE_UNAVAILABLE) is unavailable, entity_id
