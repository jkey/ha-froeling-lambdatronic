"""Tests for the button."""

from unittest.mock import MagicMock, patch

import pytest
from froeling_lambdatronic import NoResponseError
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN
from homeassistant.components.button import SERVICE_PRESS
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, snapshot_platform
from syrupy.assertion import SnapshotAssertion

from . import setup_integration
from .const import FACILITY_ID


@pytest.fixture(autouse=True)
def only_buttons() -> object:
    """Load only the button platform."""
    with patch("custom_components.froeling_lambdatronic.PLATFORMS", [Platform.BUTTON]):
        yield


@pytest.mark.usefixtures("mock_client", "mock_cloud")
async def test_entities(
    hass: HomeAssistant,
    entity_registry: er.EntityRegistry,
    snapshot: SnapshotAssertion,
    any_config_entry: MockConfigEntry,
) -> None:
    """The refresh button with its registry entry and state. for every connection type."""
    await setup_integration(hass, any_config_entry)
    await snapshot_platform(hass, entity_registry, snapshot, any_config_entry.entry_id)


async def test_press(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry_modbus: MockConfigEntry
) -> None:
    """Pressing reads the parameters again."""
    await setup_integration(hass, mock_config_entry_modbus)
    reads = mock_client.read_parameters.await_count

    await hass.services.async_call(
        BUTTON_DOMAIN,
        SERVICE_PRESS,
        {ATTR_ENTITY_ID: "button.froling_pe1_read_parameters"},
        blocking=True,
    )

    assert mock_client.read_parameters.await_count == reads + 1


async def test_press_fails_and_stays_available(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry_modbus: MockConfigEntry
) -> None:
    """A failed read is reported; the button stays available while the boiler answers."""
    await setup_integration(hass, mock_config_entry_modbus)
    mock_client.read_parameters.side_effect = NoResponseError("timeout")

    with pytest.raises(HomeAssistantError) as raised:
        await hass.services.async_call(
            BUTTON_DOMAIN,
            SERVICE_PRESS,
            {ATTR_ENTITY_ID: "button.froling_pe1_read_parameters"},
            blocking=True,
        )
    assert raised.value.translation_key == "parameters_read_failed"
    assert raised.value.translation_placeholders == {"error": "timeout"}
    await hass.async_block_till_done()
    state = hass.states.get("button.froling_pe1_read_parameters")
    assert state is not None
    assert state.state != STATE_UNAVAILABLE

    mock_client.read_parameters.side_effect = None
    await hass.services.async_call(
        BUTTON_DOMAIN,
        SERVICE_PRESS,
        {ATTR_ENTITY_ID: "button.froling_pe1_read_parameters"},
        blocking=True,
    )
    assert mock_config_entry_modbus.runtime_data.parameters.last_update_success is True


async def test_unavailable_without_boiler(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry_modbus: MockConfigEntry
) -> None:
    """The button follows the availability of the current values."""
    await setup_integration(hass, mock_config_entry_modbus)
    mock_client.read_values.side_effect = NoResponseError("timeout")

    await mock_config_entry_modbus.runtime_data.values.async_refresh()
    await hass.async_block_till_done()

    state = hass.states.get("button.froling_pe1_read_parameters")
    assert state is not None
    assert state.state == STATE_UNAVAILABLE


@pytest.mark.parametrize(
    ("entity_id", "key"),
    [
        ("button.froling_pe1_reset_ash_counter", "reset_ash_counter"),
        ("button.froling_pe1_reset_pellet_counter", "reset_pellet_counter"),
    ],
)
async def test_reset(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_cloud: MagicMock,
    mock_config_entry: MockConfigEntry,
    entity_id: str,
    key: str,
) -> None:
    """The reset buttons trigger the Fröling Connect action."""
    await setup_integration(hass, mock_config_entry)

    await hass.services.async_call(BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: entity_id}, blocking=True)

    mock_cloud.set_value.assert_awaited_once_with(FACILITY_ID, key, True)
