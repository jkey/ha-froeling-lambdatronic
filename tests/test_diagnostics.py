"""Tests for the diagnostics."""

from unittest.mock import MagicMock

import pytest
from froeling_lambdatronic import ConnectionFailedError
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import get_diagnostics_for_config_entry
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator
from syrupy.assertion import SnapshotAssertion
from syrupy.filters import props

from custom_components.froeling_lambdatronic.const import CLOUD_MAX_FAILURES

from . import setup_integration


@pytest.mark.usefixtures("mock_client", "mock_cloud")
async def test_diagnostics(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    any_config_entry: MockConfigEntry,
    snapshot: SnapshotAssertion,
) -> None:
    """Credentials, facility, equipment number and the component names given in Connect are redacted."""
    await setup_integration(hass, any_config_entry)

    result = await get_diagnostics_for_config_entry(hass, hass_client, any_config_entry)

    assert result == snapshot(exclude=props("created_at", "modified_at"))
    text = str(result)
    secrets = ("geheim", "kessel@example.com", "100000001", "Musterstraße", "Mustermann")
    names = ("Wohnbereich", "Anbau", "Brauchwasser", "Heizkreis 01", "Zirku. Pumpe")
    for secret in (*secrets, *names):
        assert secret not in text


@pytest.mark.usefixtures("mock_client")
async def test_diagnostics_last_exception(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_cloud: MagicMock,
    mock_config_entry: MockConfigEntry,
) -> None:
    """A failed update shows its message with the cause from the library, not only the exception type."""
    await setup_integration(hass, mock_config_entry)
    mock_cloud.get_overview.side_effect = ConnectionFailedError("connection refused")
    cloud = mock_config_entry.runtime_data.cloud
    for _ in range(CLOUD_MAX_FAILURES):
        await cloud.async_refresh()

    result = await get_diagnostics_for_config_entry(hass, hass_client, mock_config_entry)

    diagnostics = result["cloud"]
    assert isinstance(diagnostics, dict)
    assert diagnostics["last_update_success"] is False
    assert diagnostics["failures"] == CLOUD_MAX_FAILURES
    assert diagnostics["last_exception"] == "UpdateFailed: Reading Fröling Connect failed: connection refused"
