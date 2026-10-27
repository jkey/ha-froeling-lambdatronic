"""Tests for the number entities and the shared write path."""

from unittest.mock import MagicMock, patch

import pytest
from froeling_lambdatronic import (
    BOILER,
    AuthenticationError,
    CloudBoilerState,
    ConnectionFailedError,
    OperatingMode,
    ProtocolNotEnabledError,
    RemoteControlDisabledError,
    ValueOutOfRangeError,
    WriteRejectedError,
    heating_circuit,
)
from homeassistant.components.number import ATTR_VALUE, SERVICE_SET_VALUE
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry, snapshot_platform
from syrupy.assertion import SnapshotAssertion

from custom_components.froeling_lambdatronic.const import DOMAIN

from . import setup_integration
from .const import FACILITY_ID

BOILER_SETPOINT = "number.froling_pe1_boiler_setpoint"


@pytest.fixture(autouse=True)
def only_numbers() -> object:
    """Load only the number platform."""
    with patch("custom_components.froeling_lambdatronic.PLATFORMS", [Platform.NUMBER]):
        yield


@pytest.mark.usefixtures("mock_client", "mock_cloud")
async def test_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    any_config_entry: MockConfigEntry,
) -> None:
    """All number entities with their registry entries and states. for every connection type."""
    await setup_integration(hass, any_config_entry)
    await snapshot_platform(hass, entity_registry, snapshot, any_config_entry.entry_id)


async def _set(hass: HomeAssistant, entity_id: str, value: float) -> None:
    await hass.services.async_call(
        NUMBER_DOMAIN, SERVICE_SET_VALUE, {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: value}, blocking=True
    )


async def test_write(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry_modbus: MockConfigEntry
) -> None:
    """Writing uses the register of the library and reads the parameters again."""
    await setup_integration(hass, mock_config_entry_modbus)
    reads = mock_client.read_parameters.await_count

    await _set(hass, BOILER_SETPOINT, 75)
    await _set(hass, "number.froling_pe1_heating_circuit_02_heating_limit", 18.5)

    assert [call.args for call in mock_client.write.await_args_list] == [
        (BOILER.boiler_setpoint, 75.0),
        (heating_circuit(2).heating_limit, 18.5),
    ]
    assert mock_client.read_parameters.await_count > reads


@pytest.mark.parametrize(
    ("error", "expected", "translation_key"),
    [
        (ValueOutOfRangeError("boiler_setpoint", 75, 70, 90), ServiceValidationError, "value_out_of_range"),
        (WriteRejectedError("boiler_setpoint", "read back 140"), HomeAssistantError, "write_rejected"),
        (ConnectionFailedError("refused"), HomeAssistantError, "communication_failed"),
    ],
)
async def test_write_errors(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry_modbus: MockConfigEntry,
    error: Exception,
    expected: type[HomeAssistantError],
    translation_key: str,
) -> None:
    """Library errors become translated Home Assistant errors."""
    await setup_integration(hass, mock_config_entry_modbus)
    mock_client.write.side_effect = error

    with pytest.raises(expected) as raised:
        await _set(hass, BOILER_SETPOINT, 75)
    assert raised.value.translation_key == translation_key


async def test_protocol_disabled_issue(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry_modbus: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Illegal Function creates a repair issue that a later successful write removes."""
    await setup_integration(hass, mock_config_entry_modbus)
    issue_id = f"protocol_2014_disabled_{mock_config_entry_modbus.entry_id}"
    mock_client.write.side_effect = ProtocolNotEnabledError("boiler_setpoint", "disabled")

    with pytest.raises(HomeAssistantError) as raised:
        await _set(hass, BOILER_SETPOINT, 75)
    assert raised.value.translation_key == "protocol_disabled"
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None

    mock_client.write.side_effect = None
    await _set(hass, BOILER_SETPOINT, 75)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None


async def test_write_cloud(
    hass: HomeAssistant, mock_cloud: MagicMock, mock_config_entry_cloud: MockConfigEntry
) -> None:
    """Connect only: settings are written via Fröling Connect, which is read again afterwards."""
    await setup_integration(hass, mock_config_entry_cloud)
    reads = mock_cloud.get_overview.await_count

    await _set(hass, BOILER_SETPOINT, 75)

    mock_cloud.set_value.assert_awaited_once_with(FACILITY_ID, "boiler_setpoint", 75.0)
    assert mock_cloud.get_overview.await_count > reads


@pytest.mark.parametrize(
    ("entry", "minimum"),
    [("mock_config_entry_cloud", 40.0), ("mock_config_entry", 70.0), ("mock_config_entry_modbus", 70.0)],
)
@pytest.mark.usefixtures("mock_client", "mock_cloud")
async def test_limits_follow_write_path(
    hass: HomeAssistant, request: pytest.FixtureRequest, entry: str, minimum: float
) -> None:
    """Written via Connect: the cloud limits (boiler setpoint 40-90); via Modbus: the register (70-90)."""
    config_entry: MockConfigEntry = request.getfixturevalue(entry)
    await setup_integration(hass, config_entry)

    state = hass.states.get(BOILER_SETPOINT)
    assert state is not None
    assert state.attributes["min"] == minimum
    assert state.attributes["max"] == 90.0


@pytest.mark.parametrize(
    ("error", "expected", "translation_key"),
    [
        (ValueOutOfRangeError("boiler_setpoint", 95, 40, 90), ServiceValidationError, "value_out_of_range"),
        (WriteRejectedError("boiler_setpoint", "not editable"), HomeAssistantError, "write_rejected"),
        (ConnectionFailedError("down"), HomeAssistantError, "cloud_write_failed"),
        (RemoteControlDisabledError("off"), HomeAssistantError, "remote_control_disabled"),
        (AuthenticationError("rejected"), HomeAssistantError, "cloud_auth_failed"),
    ],
)
async def test_write_cloud_errors(
    hass: HomeAssistant,
    mock_cloud: MagicMock,
    mock_config_entry_cloud: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
    error: Exception,
    expected: type[HomeAssistantError],
    translation_key: str,
) -> None:
    """Cloud errors become translated errors; remote control and login problems have side effects."""
    await setup_integration(hass, mock_config_entry_cloud)
    mock_cloud.set_value.side_effect = error
    if isinstance(error, RemoteControlDisabledError):
        mock_cloud.get_boiler_state.return_value = CloudBoilerState(OperatingMode.AUTOMATIC, False)

    with pytest.raises(expected) as raised:
        await _set(hass, BOILER_SETPOINT, 75)
    assert raised.value.translation_key == translation_key

    await hass.async_block_till_done()
    issue = issue_registry.async_get_issue(
        DOMAIN, f"remote_control_disabled_{mock_config_entry_cloud.entry_id}"
    )
    assert (issue is not None) is isinstance(error, RemoteControlDisabledError)
    state = hass.states.get(BOILER_SETPOINT)
    assert state is not None
    assert (state.state == STATE_UNAVAILABLE) is isinstance(error, RemoteControlDisabledError)
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert bool(flows) is isinstance(error, AuthenticationError)
