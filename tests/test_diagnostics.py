"""Tests for the diagnostics."""

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import get_diagnostics_for_config_entry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator
from syrupy.assertion import SnapshotAssertion
from syrupy.filters import props

from . import setup_integration


@pytest.mark.usefixtures("mock_client", "mock_cloud")
async def test_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_config_entry: MockConfigEntry,
    snapshot: SnapshotAssertion,
) -> None:
    """Credentials are redacted; equipment and raw registers are included."""
    await setup_integration(hass, mock_config_entry)

    result = await get_diagnostics_for_config_entry(hass, hass_client, mock_config_entry)

    assert result == snapshot(exclude=props("created_at", "modified_at"))
    assert "geheim" not in str(result)
    assert "kessel@example.com" not in str(result)


async def test_diagnostics_without_cloud(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_client: MagicMock,
    mock_config_entry_modbus: MockConfigEntry,
) -> None:
    """Without Fröling Connect the cloud section is empty."""
    await setup_integration(hass, mock_config_entry_modbus)

    result = await get_diagnostics_for_config_entry(hass, hass_client, mock_config_entry_modbus)

    assert isinstance(result, dict)
    assert result["cloud"] is None
    values = result["values"]
    assert isinstance(values, dict)
    assert values["raw"] == {"30001": 126, "34001": 2, "34002": 19}


@pytest.mark.usefixtures("mock_cloud")
async def test_diagnostics_cloud_only(
    hass: HomeAssistant, hass_client: ClientSessionGenerator, mock_config_entry_cloud: MockConfigEntry
) -> None:
    """Without Modbus the Modbus sections are empty."""
    await setup_integration(hass, mock_config_entry_cloud)

    result = await get_diagnostics_for_config_entry(hass, hass_client, mock_config_entry_cloud)

    assert isinstance(result, dict)
    assert result["values"] is None
    assert result["parameters"] is None
