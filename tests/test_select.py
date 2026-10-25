"""Tests for the select entities."""

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest
from froeling_lambdatronic import (
    AuthenticationError,
    CloudBoilerState,
    ConnectionFailedError,
    OperatingMode,
    RemoteControlDisabledError,
    heating_circuit,
)
from homeassistant.components.select import ATTR_OPTION, SERVICE_SELECT_OPTION
from homeassistant.components.select import DOMAIN as SELECT_DOMAIN
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE, STATE_UNKNOWN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry, snapshot_platform
from syrupy.assertion import SnapshotAssertion

from custom_components.froeling_lambdatronic.const import DOMAIN

from . import setup_integration
from .const import FACILITY_ID, PARAMETERS, VALUES

SYSTEM_MODE = "select.froling_pe1_system_operating_mode"
HC1_MODE = "select.froling_pe1_heating_circuit_1_operating_mode"


@pytest.fixture(autouse=True)
def only_selects() -> object:
    """Load only the select platform."""
    with patch("custom_components.froeling_lambdatronic.PLATFORMS", [Platform.SELECT]):
        yield


@pytest.mark.usefixtures("mock_client", "mock_cloud")
async def test_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    mock_config_entry: MockConfigEntry,
) -> None:
    """All select entities with their registry entries and states."""
    await setup_integration(hass, mock_config_entry)
    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry.entry_id)


async def _select(hass: HomeAssistant, entity_id: str, option: str) -> None:
    await hass.services.async_call(
        SELECT_DOMAIN, SERVICE_SELECT_OPTION, {ATTR_ENTITY_ID: entity_id, ATTR_OPTION: option}, blocking=True
    )


@pytest.mark.usefixtures("mock_client")
async def test_no_system_mode_without_cloud(
    hass: HomeAssistant, mock_config_entry_local: MockConfigEntry
) -> None:
    """Without Fröling Connect there is no system operating mode select."""
    await setup_integration(hass, mock_config_entry_local)
    assert hass.states.get(SYSTEM_MODE) is None
    assert hass.states.get(HC1_MODE) is not None


async def test_heating_circuit_mode(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry_local: MockConfigEntry
) -> None:
    """The heating circuit mode is written via Modbus."""
    await setup_integration(hass, mock_config_entry_local)

    await _select(hass, HC1_MODE, "party")

    mock_client.write.assert_awaited_once_with(heating_circuit(1).operating_mode, 5)


async def test_heating_circuit_mode_unknown(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry_local: MockConfigEntry
) -> None:
    """An unknown mode read from the boiler is shown as unknown."""
    hc1 = replace(PARAMETERS.heating_circuits[1], operating_mode=None)
    mock_client.read_parameters.return_value = replace(
        PARAMETERS, heating_circuits={**PARAMETERS.heating_circuits, 1: hc1}
    )
    await setup_integration(hass, mock_config_entry_local)
    state = hass.states.get(HC1_MODE)
    assert state is not None
    assert state.state == STATE_UNKNOWN


async def test_system_mode(
    hass: HomeAssistant, mock_client: MagicMock, mock_cloud: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The system operating mode is shown from Modbus and switched via Fröling Connect."""
    await setup_integration(hass, mock_config_entry)
    state = hass.states.get(SYSTEM_MODE)
    assert state is not None
    assert state.state == "automatic"
    reads = mock_client.read_values.await_count

    await _select(hass, SYSTEM_MODE, "domestic_hot_water")

    mock_cloud.set_operating_mode.assert_awaited_once_with(FACILITY_ID, OperatingMode.DOMESTIC_HOT_WATER)
    assert mock_client.read_values.await_count > reads


async def test_system_mode_other_state(
    hass: HomeAssistant, mock_client: MagicMock, mock_cloud: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """System states that cannot be selected (e.g. cleaning) show as unknown."""
    mock_client.read_values.return_value = replace(VALUES, system_state=4)
    await setup_integration(hass, mock_config_entry)
    state = hass.states.get(SYSTEM_MODE)
    assert state is not None
    assert state.state == STATE_UNKNOWN


async def test_system_mode_remote_control_disabled(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_cloud: MagicMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Remote control switched off: select unavailable and a repair issue."""
    mock_cloud.get_boiler_state.return_value = CloudBoilerState(OperatingMode.AUTOMATIC, False)
    await setup_integration(hass, mock_config_entry)

    state = hass.states.get(SYSTEM_MODE)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE
    issue_id = f"remote_control_disabled_{mock_config_entry.entry_id}"
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None

    mock_cloud.get_boiler_state.return_value = CloudBoilerState(OperatingMode.AUTOMATIC, True)
    await mock_config_entry.runtime_data.cloud.async_refresh()
    await hass.async_block_till_done()
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None
    state = hass.states.get(SYSTEM_MODE)
    assert state is not None
    assert state.state == "automatic"


@pytest.mark.parametrize(
    ("error", "translation_key"),
    [
        (RemoteControlDisabledError("off"), "remote_control_disabled"),
        (ConnectionFailedError("down"), "cloud_write_failed"),
        (AuthenticationError("rejected"), "cloud_auth_failed"),
    ],
)
async def test_system_mode_errors(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_cloud: MagicMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
    error: Exception,
    translation_key: str,
) -> None:
    """Cloud errors become translated errors; side effects follow the error kind."""
    await setup_integration(hass, mock_config_entry)
    mock_cloud.set_operating_mode.side_effect = error
    if isinstance(error, RemoteControlDisabledError):
        mock_cloud.get_boiler_state.return_value = CloudBoilerState(OperatingMode.AUTOMATIC, False)

    with pytest.raises(HomeAssistantError) as raised:
        await _select(hass, SYSTEM_MODE, "continuous_load")
    assert raised.value.translation_key == translation_key

    issue = issue_registry.async_get_issue(DOMAIN, f"remote_control_disabled_{mock_config_entry.entry_id}")
    assert (issue is not None) is isinstance(error, RemoteControlDisabledError)
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert bool(flows) is isinstance(error, AuthenticationError)
    if flows:
        assert flows[0]["context"]["source"] == SOURCE_REAUTH


async def test_system_mode_unavailable_after_remote_control_disabled(
    hass: HomeAssistant, mock_client: MagicMock, mock_cloud: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A write refused because remote control is off makes the select unavailable at once."""
    await setup_integration(hass, mock_config_entry)
    state = hass.states.get(SYSTEM_MODE)
    assert state is not None
    assert state.state == "automatic"
    mock_cloud.set_operating_mode.side_effect = RemoteControlDisabledError("off")
    mock_cloud.get_boiler_state.return_value = CloudBoilerState(OperatingMode.AUTOMATIC, False)

    with pytest.raises(HomeAssistantError):
        await _select(hass, SYSTEM_MODE, "continuous_load")

    state = hass.states.get(SYSTEM_MODE)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE
