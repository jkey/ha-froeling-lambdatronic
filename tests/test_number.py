"""Tests for the number entities and the shared write path."""

from unittest.mock import MagicMock, patch

import pytest
from froeling_lambdatronic import (
    BOILER,
    ConnectionFailedError,
    ProtocolNotEnabledError,
    ValueOutOfRangeError,
    WriteRejectedError,
    heating_circuit,
)
from homeassistant.components.number import ATTR_VALUE, SERVICE_SET_VALUE
from homeassistant.components.number import DOMAIN as NUMBER_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry, snapshot_platform
from syrupy.assertion import SnapshotAssertion

from custom_components.froeling_lambdatronic.const import DOMAIN

from . import setup_integration

BOILER_SETPOINT = "number.froling_pe1_boiler_setpoint"


@pytest.fixture(autouse=True)
def only_numbers() -> object:
    """Load only the number platform."""
    with patch("custom_components.froeling_lambdatronic.PLATFORMS", [Platform.NUMBER]):
        yield


@pytest.mark.usefixtures("mock_client")
async def test_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    mock_config_entry_local: MockConfigEntry,
) -> None:
    """All number entities with their registry entries and states."""
    await setup_integration(hass, mock_config_entry_local)
    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry_local.entry_id)


async def _set(hass: HomeAssistant, entity_id: str, value: float) -> None:
    await hass.services.async_call(
        NUMBER_DOMAIN, SERVICE_SET_VALUE, {ATTR_ENTITY_ID: entity_id, ATTR_VALUE: value}, blocking=True
    )


async def test_write(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry_local: MockConfigEntry
) -> None:
    """Writing uses the register of the library and reads the parameters again."""
    await setup_integration(hass, mock_config_entry_local)
    reads = mock_client.read_parameters.await_count

    await _set(hass, BOILER_SETPOINT, 75)
    await _set(hass, "number.froling_pe1_heating_circuit_2_heating_limit", 18.5)

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
    mock_config_entry_local: MockConfigEntry,
    error: Exception,
    expected: type[HomeAssistantError],
    translation_key: str,
) -> None:
    """Library errors become translated Home Assistant errors."""
    await setup_integration(hass, mock_config_entry_local)
    mock_client.write.side_effect = error

    with pytest.raises(expected) as raised:
        await _set(hass, BOILER_SETPOINT, 75)
    assert raised.value.translation_key == translation_key


async def test_protocol_disabled_issue(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry_local: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Illegal Function creates a repair issue that a later successful write removes."""
    await setup_integration(hass, mock_config_entry_local)
    issue_id = f"protocol_2014_disabled_{mock_config_entry_local.entry_id}"
    mock_client.write.side_effect = ProtocolNotEnabledError("boiler_setpoint", "disabled")

    with pytest.raises(HomeAssistantError) as raised:
        await _set(hass, BOILER_SETPOINT, 75)
    assert raised.value.translation_key == "protocol_disabled"
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is not None

    mock_client.write.side_effect = None
    await _set(hass, BOILER_SETPOINT, 75)
    assert issue_registry.async_get_issue(DOMAIN, issue_id) is None
