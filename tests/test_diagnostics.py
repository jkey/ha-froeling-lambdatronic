"""Tests for the diagnostics."""

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
    any_config_entry: MockConfigEntry,
    snapshot: SnapshotAssertion,
) -> None:
    """Credentials, facility and equipment number are redacted; sources and values are included."""
    await setup_integration(hass, any_config_entry)

    result = await get_diagnostics_for_config_entry(hass, hass_client, any_config_entry)

    assert result == snapshot(exclude=props("created_at", "modified_at"))
    text = str(result)
    for secret in ("geheim", "kessel@example.com", "100000001", "Musterstraße", "Mustermann"):
        assert secret not in text
