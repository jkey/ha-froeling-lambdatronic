"""Tests for the button."""

from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, snapshot_platform
from syrupy.assertion import SnapshotAssertion

from . import setup_integration


@pytest.fixture(autouse=True)
def only_buttons() -> object:
    """Load only the button platform."""
    with patch("custom_components.froeling_lambdatronic.PLATFORMS", [Platform.BUTTON]):
        yield


@pytest.mark.usefixtures("mock_client")
async def test_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    mock_config_entry_local: MockConfigEntry,
) -> None:
    """The refresh button with its registry entry and state."""
    await setup_integration(hass, mock_config_entry_local)
    await snapshot_platform(hass, entity_registry, snapshot, mock_config_entry_local.entry_id)


async def test_press(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry_local: MockConfigEntry
) -> None:
    """Pressing reads the parameters again."""
    await setup_integration(hass, mock_config_entry_local)
    reads = mock_client.read_parameters.await_count

    await hass.services.async_call(
        BUTTON_DOMAIN,
        SERVICE_PRESS,
        {ATTR_ENTITY_ID: "button.froling_pe1_read_parameters"},
        blocking=True,
    )

    assert mock_client.read_parameters.await_count == reads + 1
