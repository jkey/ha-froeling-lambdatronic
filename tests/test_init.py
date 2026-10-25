"""Tests for setup, unload, availability and devices."""

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from froeling_lambdatronic import ConnectionFailedError, NoResponseError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.froeling_lambdatronic.const import CONF_HEATING_CIRCUITS, DOMAIN

from . import setup_integration
from .const import ENTRY_OPTIONS


async def test_setup_and_unload(
    hass: HomeAssistant, mock_client: MagicMock, mock_cloud: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """The entry loads, reads values and parameters, and closes the client on unload."""
    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    mock_client.connect.assert_awaited_once()
    mock_client.read_values.assert_awaited_once()
    mock_client.read_parameters.assert_awaited_once()
    mock_cloud.get_boiler_state.assert_awaited_once_with(4711)

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
    mock_client.close.assert_awaited_once()


async def test_setup_connect_fails(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry_local: MockConfigEntry
) -> None:
    """An unreachable gateway makes HA retry later."""
    mock_client.connect.side_effect = ConnectionFailedError("refused")

    await setup_integration(hass, mock_config_entry_local)

    assert mock_config_entry_local.state is ConfigEntryState.SETUP_RETRY
    mock_client.close.assert_awaited_once()


async def test_setup_first_read_fails(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry_local: MockConfigEntry
) -> None:
    """A boiler that does not answer makes HA retry later."""
    mock_client.read_values.side_effect = NoResponseError("timeout")

    await setup_integration(hass, mock_config_entry_local)

    assert mock_config_entry_local.state is ConfigEntryState.SETUP_RETRY
    mock_client.close.assert_awaited_once()


async def test_unavailable_and_recovery_logged_once(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry_local: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Modbus entities become unavailable; failure and recovery are logged once each."""
    await setup_integration(hass, mock_config_entry_local)
    state = hass.states.get("sensor.froling_pe1_boiler_temperature")
    assert state is not None
    assert state.state == "63.0"

    mock_client.read_values.side_effect = NoResponseError("timeout")
    for _ in range(2):
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
        await hass.async_block_till_done()
    state = hass.states.get("sensor.froling_pe1_boiler_temperature")
    assert state is not None
    assert state.state == STATE_UNAVAILABLE
    assert caplog.text.count("Error fetching froeling_lambdatronic values data") == 1

    mock_client.read_values.side_effect = None
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=95))
    await hass.async_block_till_done()
    state = hass.states.get("sensor.froling_pe1_boiler_temperature")
    assert state is not None
    assert state.state == "63.0"
    assert caplog.text.count("Fetching froeling_lambdatronic values data recovered") == 1


async def test_cloud_failure_does_not_block_setup(
    hass: HomeAssistant, mock_client: MagicMock, mock_cloud: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Fröling Connect being down leaves the Modbus part working."""
    mock_cloud.get_boiler_state.side_effect = ConnectionFailedError("down")

    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    cloud = mock_config_entry.runtime_data.cloud
    assert cloud is not None
    assert cloud.last_update_success is False
    state = hass.states.get("sensor.froling_pe1_boiler_temperature")
    assert state is not None
    assert state.state == "63.0"


async def test_devices(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry_local: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """The boiler device and one child device per component."""
    await setup_integration(hass, mock_config_entry_local)

    entry_id = mock_config_entry_local.entry_id
    boiler = device_registry.async_get_device_by_identifier((DOMAIN, entry_id), entry_id)
    assert boiler is not None
    assert boiler.name == "Fröling PE1"
    assert boiler.manufacturer == "Fröling"
    assert boiler.model == "Lambdatronic 3200"
    names = {}
    for suffix in ("hc1", "hc2", "dhw1", "buffer1"):
        child = device_registry.async_get_child_device_by_identifier(
            (DOMAIN, f"{entry_id}_{suffix}"), entry_id
        )
        assert child is not None
        assert child.parent_device_id == boiler.id
        names[suffix] = child.name
    assert names == {
        "hc1": "Heating circuit 1",
        "hc2": "Heating circuit 2",
        "dhw1": "Hot water 1",
        "buffer1": "Buffer 1",
    }


async def test_stale_devices_removed(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry_local: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Disabling a heating circuit in the options removes its child device on reload."""
    await setup_integration(hass, mock_config_entry_local)
    entry_id = mock_config_entry_local.entry_id
    assert device_registry.async_get_child_device_by_identifier((DOMAIN, f"{entry_id}_hc2"), entry_id)

    hass.config_entries.async_update_entry(
        mock_config_entry_local, options={**ENTRY_OPTIONS, CONF_HEATING_CIRCUITS: [1]}
    )
    await hass.config_entries.async_reload(entry_id)
    await hass.async_block_till_done()

    assert device_registry.async_get_child_device_by_identifier((DOMAIN, f"{entry_id}_hc2"), entry_id) is None
    assert device_registry.async_get_child_device_by_identifier((DOMAIN, f"{entry_id}_hc1"), entry_id)


async def test_remove_config_entry_device(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    mock_client: MagicMock,
    mock_config_entry_local: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Only devices of components that are no longer enabled can be deleted manually."""
    assert await async_setup_component(hass, "config", {})
    await setup_integration(hass, mock_config_entry_local)
    entry_id = mock_config_entry_local.entry_id
    boiler = device_registry.async_get_device_by_identifier((DOMAIN, entry_id), entry_id)
    assert boiler is not None
    stale = device_registry.async_get_or_create_child(
        config_entry_id=entry_id, identifiers={(DOMAIN, f"{entry_id}_hc7")}, parent_device_id=boiler.id
    )
    active = device_registry.async_get_child_device_by_identifier((DOMAIN, f"{entry_id}_hc1"), entry_id)
    assert active is not None

    client = await hass_ws_client(hass)
    for device_id, removable in ((stale.id, True), (active.id, False)):
        await client.send_json_auto_id({"type": "config/device_registry/remove", "device_id": device_id})
        response = await client.receive_json()
        assert response["success"] is removable
