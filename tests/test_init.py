"""Tests for setup, unload, availability, sources and devices."""

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from froeling_lambdatronic import (
    AuthenticationError,
    ConnectionFailedError,
    NoResponseError,
    RateLimitedError,
)
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.froeling_lambdatronic.const import (
    CONF_CIRCULATION_PUMP,
    CONF_EQUIPMENT,
    CONF_HEATING_CIRCUITS,
    DOMAIN,
    ConnectionType,
    connection_type,
)
from custom_components.froeling_lambdatronic.sources import Source

from . import setup_integration
from .const import CLOUD_COMPONENTS, EQUIPMENT_DATA, FACILITY_ID, entry_data

BOILER_TEMPERATURE = "sensor.froling_pe1_boiler_temperature"


@pytest.mark.parametrize(
    ("connection", "expected"),
    [
        (ConnectionType.MODBUS, ConnectionType.MODBUS),
        (ConnectionType.CLOUD, ConnectionType.CLOUD),
        (ConnectionType.HYBRID, ConnectionType.HYBRID),
    ],
)
def test_connection_type(connection: ConnectionType, expected: ConnectionType) -> None:
    """The connection type follows from the Modbus host and the Connect login in the data."""
    assert connection_type(entry_data(connection)) is expected


async def test_setup_and_unload_hybrid(
    hass: HomeAssistant, mock_client: MagicMock, mock_cloud: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Hybrid: Modbus values and parameters, the cloud overview, state, errors and system info."""
    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    mock_client.connect.assert_awaited_once()
    mock_client.read_values.assert_awaited_once()
    mock_client.read_parameters.assert_awaited_once()
    mock_cloud.get_overview.assert_awaited_once_with(FACILITY_ID)
    mock_cloud.get_component_detail.assert_not_awaited()
    mock_cloud.get_boiler_state.assert_awaited_once_with(FACILITY_ID)
    mock_cloud.get_errors.assert_awaited_once_with(FACILITY_ID)
    mock_cloud.get_system_info.assert_awaited_once_with(FACILITY_ID)
    mock_cloud.get_header.assert_awaited_once_with(FACILITY_ID)

    await hass.config_entries.async_unload(mock_config_entry.entry_id)
    assert mock_config_entry.state is ConfigEntryState.NOT_LOADED
    mock_client.close.assert_awaited_once()


async def test_setup_and_unload_cloud(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_cloud: MagicMock,
    mock_config_entry_cloud: MockConfigEntry,
) -> None:
    """Connect only: every component detail is read, Modbus is not used."""
    await setup_integration(hass, mock_config_entry_cloud)

    assert mock_config_entry_cloud.state is ConfigEntryState.LOADED
    mock_cloud.get_overview.assert_awaited_once_with(FACILITY_ID)
    assert mock_cloud.get_component_detail.await_count == len(CLOUD_COMPONENTS)
    mock_client.connect.assert_not_awaited()
    data = mock_config_entry_cloud.runtime_data
    assert data.modbus is None
    assert data.values is None
    assert data.parameters is None

    await hass.config_entries.async_unload(mock_config_entry_cloud.entry_id)
    assert mock_config_entry_cloud.state is ConfigEntryState.NOT_LOADED
    mock_client.close.assert_not_awaited()


async def test_setup_modbus(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry_modbus: MockConfigEntry
) -> None:
    """Modbus only: no cloud objects."""
    await setup_integration(hass, mock_config_entry_modbus)

    data = mock_config_entry_modbus.runtime_data
    assert mock_config_entry_modbus.state is ConfigEntryState.LOADED
    assert data.cloud is None
    assert data.cloud_info is None
    assert data.cloud_client is None


async def test_setup_connect_fails(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry_modbus: MockConfigEntry
) -> None:
    """An unreachable gateway makes HA retry later."""
    mock_client.connect.side_effect = ConnectionFailedError("refused")

    await setup_integration(hass, mock_config_entry_modbus)

    assert mock_config_entry_modbus.state is ConfigEntryState.SETUP_RETRY
    mock_client.close.assert_awaited_once()


async def test_setup_first_read_fails(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry_modbus: MockConfigEntry
) -> None:
    """A boiler that does not answer makes HA retry later."""
    mock_client.read_values.side_effect = NoResponseError("timeout")

    await setup_integration(hass, mock_config_entry_modbus)

    assert mock_config_entry_modbus.state is ConfigEntryState.SETUP_RETRY
    mock_client.close.assert_awaited_once()


async def test_setup_cloud_fails(
    hass: HomeAssistant, mock_cloud: MagicMock, mock_config_entry_cloud: MockConfigEntry
) -> None:
    """Connect only: an unreachable cloud makes HA retry later."""
    mock_cloud.get_overview.side_effect = ConnectionFailedError("down")

    await setup_integration(hass, mock_config_entry_cloud)

    assert mock_config_entry_cloud.state is ConfigEntryState.SETUP_RETRY


async def test_setup_cloud_auth_fails(
    hass: HomeAssistant, mock_cloud: MagicMock, mock_config_entry_cloud: MockConfigEntry
) -> None:
    """Connect only: a rejected login stops the setup and starts the reauth flow."""
    mock_cloud.get_overview.side_effect = AuthenticationError("rejected")

    await setup_integration(hass, mock_config_entry_cloud)

    assert mock_config_entry_cloud.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_unavailable_and_recovery_logged_once(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry_modbus: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Modbus entities become unavailable; failure and recovery are logged once each."""
    await setup_integration(hass, mock_config_entry_modbus)
    state = hass.states.get(BOILER_TEMPERATURE)
    assert state is not None
    assert state.state == "63.0"

    mock_client.read_values.side_effect = NoResponseError("timeout")
    for _ in range(2):
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=31))
        await hass.async_block_till_done()
    state = hass.states.get(BOILER_TEMPERATURE)
    assert state is not None
    assert state.state == STATE_UNAVAILABLE
    assert caplog.text.count("Error fetching froeling_lambdatronic values data") == 1

    mock_client.read_values.side_effect = None
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=95))
    await hass.async_block_till_done()
    state = hass.states.get(BOILER_TEMPERATURE)
    assert state is not None
    assert state.state == "63.0"
    assert caplog.text.count("Fetching froeling_lambdatronic values data recovered") == 1


async def test_sources_hybrid(
    hass: HomeAssistant, mock_client: MagicMock, mock_cloud: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Modbus wins; the cloud fills only the values Modbus does not have."""
    await setup_integration(hass, mock_config_entry)
    sources = mock_config_entry.runtime_data.sources

    assert sources.source("boiler_temperature") is Source.MODBUS_VALUES
    assert sources.source("boiler_setpoint") is Source.MODBUS_PARAMETERS
    assert sources.source("hc1_pump") is Source.CLOUD
    assert sources.source("dhw1_mode") is Source.CLOUD
    assert sources.source("induced_draft_control") is None
    assert sources.value("boiler_temperature") == 63.0
    assert sources.value("buffer1_charge") == 66.0
    assert sources.value("unknown_key") is None
    assert sources.write_source("boiler_setpoint") is Source.MODBUS_PARAMETERS
    assert sources.write_source("system_state") is Source.CLOUD
    assert sources.write_source("pellet_filling_start_2") is Source.CLOUD
    assert not sources.writable("unknown_key")
    # Limits of cloud parameters are only known with component details (Connect only).
    assert sources.cloud_limits("boiler_setpoint") is None
    assert sources.cloud_limits("unknown_key") is None


async def test_sources_cloud(
    hass: HomeAssistant, mock_cloud: MagicMock, mock_config_entry_cloud: MockConfigEntry
) -> None:
    """Connect only: all values from the cloud, nothing is written via Modbus."""
    await setup_integration(hass, mock_config_entry_cloud)
    sources = mock_config_entry_cloud.runtime_data.sources

    assert sources.source("boiler_temperature") is Source.CLOUD
    assert sources.source("induced_draft_control") is Source.CLOUD
    assert sources.source("boiler_control_output") is None
    assert sources.value("boiler_temperature") == 61.0
    assert sources.write_source("boiler_setpoint") is Source.CLOUD
    assert sources.write_source("hc1_room_setpoint_heating") is None
    assert sources.cloud_limits("boiler_setpoint") == (40.0, 90.0)


async def test_sources_modbus(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry_modbus: MockConfigEntry
) -> None:
    """Modbus only: read-only registers and cloud-only values have no way to be written."""
    await setup_integration(hass, mock_config_entry_modbus)
    sources = mock_config_entry_modbus.runtime_data.sources

    assert sources.source("hc1_pump") is None
    assert sources.value("hc1_pump") is None
    assert sources.write_source("pellet_filling_start_2") is None
    assert sources.write_source("system_state") is None
    assert not sources.remote_control_allowed
    with pytest.raises(ValueError, match="not configured"):
        sources.coordinator(Source.CLOUD)
    with pytest.raises(HomeAssistantError) as raised:
        await sources.async_write("system_state", 2)
    assert raised.value.translation_key == "not_writable"


async def test_hybrid_cloud_failure_does_not_block_setup(
    hass: HomeAssistant, mock_client: MagicMock, mock_cloud: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Fröling Connect being down leaves the Modbus part working; cloud values still get a source."""
    mock_cloud.get_overview.side_effect = ConnectionFailedError("down")

    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    data = mock_config_entry.runtime_data
    assert data.cloud is not None
    assert data.cloud.last_update_success is False
    assert data.sources.source("hc1_pump") is Source.CLOUD
    assert data.sources.value("hc1_pump") is None
    state = hass.states.get(BOILER_TEMPERATURE)
    assert state is not None
    assert state.state == "63.0"


async def test_cloud_rate_limit_retry_after(
    hass: HomeAssistant, mock_client: MagicMock, mock_cloud: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """HTTP 429 makes the coordinator wait as long as Fröling Connect asks for."""
    await setup_integration(hass, mock_config_entry)
    cloud = mock_config_entry.runtime_data.cloud
    assert cloud is not None
    mock_cloud.get_overview.side_effect = RateLimitedError(600)

    await cloud.async_refresh()

    assert cloud.last_update_success is False
    assert isinstance(cloud.last_exception, UpdateFailed)
    assert cloud.last_exception.retry_after == 600


async def test_cloud_skips_disabled_devices(
    hass: HomeAssistant,
    mock_cloud: MagicMock,
    mock_config_entry_cloud: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Connect only: details of disabled child devices are not read; their keys keep a source."""
    await setup_integration(hass, mock_config_entry_cloud)
    entry_id = mock_config_entry_cloud.entry_id
    hc2 = device_registry.async_get_child_device_by_identifier((DOMAIN, f"{entry_id}_hc2"), entry_id)
    assert hc2 is not None
    device_registry.async_update_child_device(hc2.id, disabled_by=dr.DeviceEntryDisabler.USER)
    await hass.config_entries.async_reload(entry_id)
    await hass.async_block_till_done()

    mock_cloud.get_component_detail.reset_mock()
    await mock_config_entry_cloud.runtime_data.cloud.async_refresh()
    read = {call.args[1] for call in mock_cloud.get_component_detail.await_args_list}
    assert read == {c.component_id for c in CLOUD_COMPONENTS} - {"300_3110"}
    sources = mock_config_entry_cloud.runtime_data.sources
    assert sources.source("hc2_curve_flow_plus10") is Source.CLOUD
    assert sources.value("hc2_curve_flow_plus10") is None
    assert sources.source("hc2_room_setpoint_heating") is None


async def test_devices_modbus(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry_modbus: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Without Connect: the boiler and child devices named by the Fröling scheme."""
    await setup_integration(hass, mock_config_entry_modbus)

    entry_id = mock_config_entry_modbus.entry_id
    boiler = device_registry.async_get_device_by_identifier((DOMAIN, entry_id), entry_id)
    assert boiler is not None
    assert boiler.name == "Fröling PE1"
    assert boiler.manufacturer == "Fröling"
    assert boiler.model == "Lambdatronic 3200"
    assert boiler.serial_number is None
    assert _child_names(device_registry, entry_id, boiler.id) == {
        "hc1": "Heating circuit 01",
        "hc2": "Heating circuit 02",
        "dhw1": "DHW tank 01",
        "buffer1": "Buffer 01",
        "circulation": "Circulation pump",
    }


async def test_devices_connect(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_cloud: MagicMock,
    mock_config_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """With Connect: names from Fröling Connect, system information at the boiler; own names stay."""
    mock_config_entry.add_to_hass(hass)
    entry_id = mock_config_entry.entry_id
    boiler = device_registry.async_get_or_create(
        config_entry_id=entry_id, identifiers={(DOMAIN, entry_id)}, name="Fröling PE1"
    )
    child = device_registry.async_get_or_create_child(
        config_entry_id=entry_id,
        identifiers={(DOMAIN, f"{entry_id}_hc1")},
        parent_device_id=boiler.id,
        name="Heating circuit 01",
    )
    device_registry.async_update_child_device(child.id, name_by_user="Wohnzimmer")

    await setup_integration(hass, mock_config_entry)

    boiler_entry = device_registry.async_get_device_by_identifier((DOMAIN, entry_id), entry_id)
    assert boiler_entry is not None
    assert boiler_entry.model == "PE1 Pellet 20"
    assert boiler_entry.serial_number == "100000001"
    assert boiler_entry.sw_version == "V 50.04 B 05.17 (05.04.2019)"
    assert boiler_entry.hw_version == "V 60.01 B 01.35.30"
    assert _child_names(device_registry, entry_id, boiler.id) == {
        "hc1": "Wohnbereich",
        "hc2": "Anbau",
        "dhw1": "Brauchwasser",
        "buffer1": "Pufferspeicher",
        "circulation": "Zirku. Pumpe",
    }
    hc1 = device_registry.async_get_child_device_by_identifier((DOMAIN, f"{entry_id}_hc1"), entry_id)
    assert hc1 is not None
    assert hc1.name_by_user == "Wohnzimmer"


def _child_names(registry: dr.DeviceRegistry, entry_id: str, boiler_id: str) -> dict[str, str | None]:
    names: dict[str, str | None] = {}
    for suffix in ("hc1", "hc2", "dhw1", "buffer1", "circulation"):
        child = registry.async_get_child_device_by_identifier((DOMAIN, f"{entry_id}_{suffix}"), entry_id)
        assert child is not None
        assert child.parent_device_id == boiler_id
        names[suffix] = child.name
    return names


async def test_stale_devices_removed(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry_modbus: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """A component missing from the equipment loses its child device on reload."""
    await setup_integration(hass, mock_config_entry_modbus)
    entry_id = mock_config_entry_modbus.entry_id
    assert device_registry.async_get_child_device_by_identifier((DOMAIN, f"{entry_id}_hc2"), entry_id)

    hass.config_entries.async_update_entry(
        mock_config_entry_modbus,
        data={
            **mock_config_entry_modbus.data,
            CONF_EQUIPMENT: {**EQUIPMENT_DATA, CONF_HEATING_CIRCUITS: [1], CONF_CIRCULATION_PUMP: False},
        },
    )
    await hass.config_entries.async_reload(entry_id)
    await hass.async_block_till_done()

    for suffix in ("hc2", "circulation"):
        assert (
            device_registry.async_get_child_device_by_identifier((DOMAIN, f"{entry_id}_{suffix}"), entry_id)
            is None
        )
    assert device_registry.async_get_child_device_by_identifier((DOMAIN, f"{entry_id}_hc1"), entry_id)


async def test_remove_config_entry_device(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    mock_client: MagicMock,
    mock_config_entry_modbus: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Only devices of components that are no longer installed can be deleted manually."""
    assert await async_setup_component(hass, "config", {})
    await setup_integration(hass, mock_config_entry_modbus)
    entry_id = mock_config_entry_modbus.entry_id
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


async def test_cloud_auth_failure_starts_reauth(
    hass: HomeAssistant, mock_client: MagicMock, mock_cloud: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Hybrid: a rejected Connect login keeps the entry loaded and starts the reauth flow."""
    mock_cloud.get_boiler_state.side_effect = AuthenticationError("rejected")

    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_cloud_info_failure(
    hass: HomeAssistant, mock_client: MagicMock, mock_cloud: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Missing system information does not block the setup."""
    mock_cloud.get_system_info.side_effect = ConnectionFailedError("down")

    await setup_integration(hass, mock_config_entry)

    assert mock_config_entry.state is ConfigEntryState.LOADED
    cloud_info = mock_config_entry.runtime_data.cloud_info
    assert cloud_info is not None
    assert cloud_info.last_update_success is False


async def test_without_cloud_removes_cloud_leftovers(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_config_entry_modbus: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """After Connect was removed, its repair issue and the system mode select disappear."""
    mock_config_entry_modbus.add_to_hass(hass)
    entry_id = mock_config_entry_modbus.entry_id
    select = entity_registry.async_get_or_create(
        "select", DOMAIN, f"{entry_id}_system_operating_mode", config_entry=mock_config_entry_modbus
    )
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"remote_control_disabled_{entry_id}",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key="remote_control_disabled",
    )

    await setup_integration(hass, mock_config_entry_modbus)

    assert mock_config_entry_modbus.state is ConfigEntryState.LOADED
    assert entity_registry.async_get(select.entity_id) is None
    assert issue_registry.async_get_issue(DOMAIN, f"remote_control_disabled_{entry_id}") is None


async def test_remove_entry_deletes_issues(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_cloud: MagicMock,
    mock_config_entry: MockConfigEntry,
    issue_registry: ir.IssueRegistry,
) -> None:
    """Removing the entry deletes its repair issues."""
    await setup_integration(hass, mock_config_entry)
    entry_id = mock_config_entry.entry_id
    for issue in ("remote_control_disabled", "protocol_2014_disabled"):
        ir.async_create_issue(
            hass,
            DOMAIN,
            f"{issue}_{entry_id}",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=issue,
        )

    await hass.config_entries.async_remove(entry_id)
    await hass.async_block_till_done()

    for issue in ("remote_control_disabled", "protocol_2014_disabled"):
        assert issue_registry.async_get_issue(DOMAIN, f"{issue}_{entry_id}") is None
