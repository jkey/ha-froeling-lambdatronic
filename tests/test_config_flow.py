"""Tests for the config, reconfigure, reauth and options flows."""

from collections.abc import Mapping
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from froeling_lambdatronic import (
    AuthenticationError,
    CommunicationError,
    ConnectionFailedError,
    Equipment,
    Facility,
    NoResponseError,
)
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.froeling_lambdatronic.const import (
    CONF_CLOUD,
    CONF_CLOUD_INTERVAL,
    CONF_CLOUD_KEYS,
    CONF_EQUIPMENT,
    CONF_FACILITY_ID,
    CONF_HEATING_CIRCUITS,
    CONF_PARAMETER_INTERVAL,
    CONF_ROOM_SENSORS,
    CONF_SCAN_INTERVAL,
    CONF_SLAVE,
    DOMAIN,
    ConnectionType,
    connection_type,
)

from . import setup_integration
from .const import (
    CLOUD_UNIQUE_ID,
    EQUIPMENT_DATA,
    FACILITY_ID,
    HOST,
    PASSWORD,
    UNIQUE_ID,
    USERNAME,
    cloud_components,
    entry_data,
)

MODBUS_INPUT = {CONF_HOST: HOST, "advanced": {CONF_PORT: 502, CONF_SLAVE: 2}}
CLOUD_INPUT = {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD}
FACILITY = Facility(FACILITY_ID, "Home", "PE1 Pellet 20", "100000001")


def _without_cloud_keys(data: Mapping[str, Any]) -> dict[str, Any]:
    assert CONF_CLOUD_KEYS in data
    return {key: value for key, value in data.items() if key != CONF_CLOUD_KEYS}


async def _start(hass: HomeAssistant, connection: ConnectionType) -> dict[str, Any]:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["modbus", "cloud", "hybrid"]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": connection})
    assert result["type"] is FlowResultType.FORM
    return dict(result)


@pytest.mark.usefixtures("mock_client")
async def test_modbus(hass: HomeAssistant) -> None:
    """Modbus only: host, port and slave; equipment detected; title 'Fröling'."""
    result = await _start(hass, ConnectionType.MODBUS)
    assert result["step_id"] == "modbus"

    result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], MODBUS_INPUT))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Fröling"
    assert result["data"] == entry_data(ConnectionType.MODBUS)
    assert result["options"] == {CONF_SCAN_INTERVAL: 30, CONF_PARAMETER_INTERVAL: 5}
    assert result["result"].unique_id == UNIQUE_ID


@pytest.mark.usefixtures("mock_cloud")
async def test_cloud(hass: HomeAssistant) -> None:
    """Connect only: equipment from the component list, title from the boiler type."""
    result = await _start(hass, ConnectionType.CLOUD)
    assert result["step_id"] == "cloud"

    result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], CLOUD_INPUT))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Fröling PE1 Pellet 20"
    assert result["data"] == entry_data(ConnectionType.CLOUD)
    assert result["options"] == {CONF_CLOUD_INTERVAL: 2}
    assert result["result"].unique_id == CLOUD_UNIQUE_ID
    await hass.async_block_till_done()
    assert result["result"].state is ConfigEntryState.LOADED


@pytest.mark.usefixtures("mock_client", "mock_cloud")
async def test_hybrid(hass: HomeAssistant) -> None:
    """Modbus, then Connect; the same components need no confirmation."""
    result = await _start(hass, ConnectionType.HYBRID)
    assert result["step_id"] == "modbus"
    result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], MODBUS_INPUT))
    assert result["step_id"] == "cloud"

    result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], CLOUD_INPUT))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Fröling PE1 Pellet 20"
    assert result["data"] == entry_data(ConnectionType.HYBRID)
    assert result["options"] == {CONF_SCAN_INTERVAL: 30, CONF_PARAMETER_INTERVAL: 5, CONF_CLOUD_INTERVAL: 2}
    assert result["result"].unique_id == UNIQUE_ID


@pytest.mark.usefixtures("mock_cloud")
async def test_hybrid_mismatch(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """Different components are shown for confirmation; all of them are set up."""
    mock_client.detect_equipment.return_value = Equipment(
        heating_circuits=(1, 2, 3), hot_water=(1,), buffers=(1,), circulation_pump=False
    )
    result = await _start(hass, ConnectionType.HYBRID)
    result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], MODBUS_INPUT))
    result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], CLOUD_INPUT))
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "mismatch"
    assert result["description_placeholders"] == {"components": "Heating circuit 03, Circulation pump"}

    result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], {}))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_EQUIPMENT] == {**EQUIPMENT_DATA, CONF_HEATING_CIRCUITS: [1, 2, 3]}


@pytest.mark.usefixtures("mock_client")
@pytest.mark.parametrize("connection", [ConnectionType.CLOUD, ConnectionType.HYBRID])
@pytest.mark.parametrize(
    ("unknown", "details", "expected", "detail_reads"),
    [
        # Sub type 'OUT_TEMP_CRTL' has no room sensor; no detail is read.
        ((), {}, [], 0),
        # Otherwise only the picture URL of the detail tells.
        ((1, 2), {"hc1": True, "hc2": False}, [1], 2),
        ((2,), {"hc2": True}, [2], 1),
        ((1,), {"hc1": None}, [], 1),
    ],
)
async def test_room_sensors(
    hass: HomeAssistant,
    mock_cloud: MagicMock,
    connection: ConnectionType,
    unknown: tuple[int, ...],
    details: dict[str, bool | None],
    expected: list[int],
    detail_reads: int,
) -> None:
    """Heating circuits with a room sensor: from the component detail where the list does not tell."""
    mock_cloud.get_components.return_value = cloud_components(unknown)
    mock_cloud.detail_room_sensors = details
    result = await _start(hass, connection)
    if connection is ConnectionType.HYBRID:
        result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], MODBUS_INPUT))

    result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], CLOUD_INPUT))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ROOM_SENSORS] == expected
    if connection is ConnectionType.HYBRID:
        # Connect only: the setup of the new entry reads every detail as well.
        assert mock_cloud.get_component_detail.await_count == detail_reads


@pytest.mark.usefixtures("mock_client")
async def test_room_sensors_detail_fails(hass: HomeAssistant, mock_cloud: MagicMock) -> None:
    """A failed detail read stores no room sensors at all: the setup detects them later."""
    mock_cloud.get_components.return_value = cloud_components((1, 2))
    mock_cloud.detail_room_sensors = {"hc2": True}
    detail = mock_cloud.get_component_detail.side_effect
    mock_cloud.get_component_detail.side_effect = lambda facility_id, component_id: (
        _raise(ConnectionFailedError("down"))
        if component_id == "300_3100"
        else detail(facility_id, component_id)
    )
    result = await _start(hass, ConnectionType.HYBRID)
    result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], MODBUS_INPUT))

    result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], CLOUD_INPUT))

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert CONF_ROOM_SENSORS not in result["data"]
    # The setup of the new entry detected them from its first refresh (overview: 'OUT_TEMP_CRTL').
    entry = hass.config_entries.async_get_entry(result["result"].entry_id)
    assert entry is not None
    assert entry.data[CONF_ROOM_SENSORS] == []


def _raise(err: Exception) -> None:
    raise err


async def test_multiple_facilities(hass: HomeAssistant, mock_cloud: MagicMock) -> None:
    """With several facilities the user picks one (name and boiler type)."""
    mock_cloud.get_facilities.return_value = [
        Facility(1, "Ferienhaus", None),
        Facility(2, "", None),
        FACILITY,
    ]
    result = await _start(hass, ConnectionType.CLOUD)
    result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], CLOUD_INPUT))
    assert result["step_id"] == "facility"
    options = result["data_schema"].schema[CONF_FACILITY_ID].config["options"]
    assert [option["label"] for option in options] == ["Ferienhaus", "2", "Home (PE1 Pellet 20)"]

    mock_cloud.get_components.side_effect = ConnectionFailedError("down")
    result = dict(
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_FACILITY_ID: str(FACILITY_ID)}
        )
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cloud_cannot_connect"}

    mock_cloud.get_components.side_effect = None
    result = dict(
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_FACILITY_ID: str(FACILITY_ID)}
        )
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CLOUD][CONF_FACILITY_ID] == FACILITY_ID


@pytest.mark.usefixtures("mock_cloud")
async def test_title_without_product_type(hass: HomeAssistant, mock_cloud: MagicMock) -> None:
    """Without a boiler type the title is "Fröling"; the Connect facility name is never adopted."""
    mock_cloud.get_facilities.return_value = [Facility(FACILITY_ID, "Home", None)]
    result = await _start(hass, ConnectionType.CLOUD)
    result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], CLOUD_INPUT))
    assert result["title"] == "Fröling"


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (NoResponseError("timeout"), "no_response"),
        (ConnectionFailedError("refused"), "cannot_connect"),
        (CommunicationError("exception 0x0B"), "invalid_slave"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_modbus_errors(
    hass: HomeAssistant, mock_client: MagicMock, error: Exception, expected: str
) -> None:
    """Connection errors are shown and the user can try again."""
    mock_client.probe.side_effect = error
    result = await _start(hass, ConnectionType.MODBUS)
    result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], MODBUS_INPUT))
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}
    mock_client.close.assert_awaited()

    mock_client.probe.side_effect = None
    result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], MODBUS_INPUT))
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize(
    ("method", "side_effect", "expected"),
    [
        ("login", AuthenticationError("rejected"), "invalid_auth"),
        ("get_facilities", ConnectionFailedError("down"), "cloud_cannot_connect"),
        ("get_facilities", [[]], "no_facilities"),
        ("get_components", AuthenticationError("rejected"), "invalid_auth"),
        ("login", RuntimeError("boom"), "unknown"),
    ],
)
async def test_cloud_errors(
    hass: HomeAssistant, mock_cloud: MagicMock, method: str, side_effect: object, expected: str
) -> None:
    """Fröling Connect errors are shown; afterwards the login succeeds."""
    result = await _start(hass, ConnectionType.CLOUD)
    getattr(mock_cloud, method).side_effect = side_effect

    result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], CLOUD_INPUT))
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}

    getattr(mock_cloud, method).side_effect = None
    result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], CLOUD_INPUT))
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.usefixtures("mock_client")
async def test_modbus_already_configured(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    """The same gateway and slave cannot be added twice."""
    mock_config_entry.add_to_hass(hass)
    result = await _start(hass, ConnectionType.MODBUS)
    result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], MODBUS_INPUT))
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.usefixtures("mock_cloud")
async def test_facility_already_configured(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    """A Connect facility may only be used by one entry, also next to Modbus."""
    mock_config_entry.add_to_hass(hass)
    result = await _start(hass, ConnectionType.CLOUD)
    result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], CLOUD_INPUT))
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.usefixtures("mock_client")
async def test_reauth(hass: HomeAssistant, mock_cloud: MagicMock, mock_config_entry: MockConfigEntry) -> None:
    """New Fröling Connect credentials are stored and the entry reloads."""
    await setup_integration(hass, mock_config_entry)
    result: dict[str, Any] = dict(await mock_config_entry.start_reauth_flow(hass))
    assert result["step_id"] == "reauth_confirm"

    for side_effect, facilities, expected in (
        (AuthenticationError("rejected"), [FACILITY], "invalid_auth"),
        (RuntimeError("boom"), [FACILITY], "unknown"),
        (None, [Facility(1, "Ferienhaus", None)], "facility_missing"),
    ):
        mock_cloud.login.side_effect = side_effect
        mock_cloud.get_facilities.return_value = facilities
        result = dict(
            await hass.config_entries.flow.async_configure(
                result["flow_id"], {CONF_USERNAME: USERNAME, CONF_PASSWORD: "falsch"}
            )
        )
        assert result["errors"] == {"base": expected}

    mock_cloud.get_facilities.return_value = [FACILITY]
    result = dict(
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_USERNAME: USERNAME, CONF_PASSWORD: "neu"}
        )
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_CLOUD] == {
        CONF_USERNAME: USERNAME,
        CONF_PASSWORD: "neu",
        CONF_FACILITY_ID: FACILITY_ID,
    }


async def _reconfigure(hass: HomeAssistant, entry: MockConfigEntry, option: str) -> dict[str, Any]:
    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.MENU
    return dict(await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": option}))


@pytest.mark.usefixtures("mock_client", "mock_cloud")
async def test_reconfigure_menu(hass: HomeAssistant, any_config_entry: MockConfigEntry) -> None:
    """Removing a connection is only offered while the other one stays."""
    any_config_entry.add_to_hass(hass)
    result = await any_config_entry.start_reconfigure_flow(hass)
    expected = ["reconfigure_modbus", "reconfigure_cloud"]
    if connection_type(any_config_entry.data) is ConnectionType.HYBRID:
        expected += ["remove_cloud", "remove_modbus"]
    assert result["menu_options"] == expected


@pytest.mark.usefixtures("mock_cloud")
async def test_reconfigure_same_gateway_reuses_client(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Unchanged connection: the running client is used; equipment detected again."""
    await setup_integration(hass, mock_config_entry)
    result = await _reconfigure(hass, mock_config_entry, "reconfigure_modbus")
    assert result["step_id"] == "reconfigure_modbus"
    mock_client.detect_equipment.return_value = Equipment(
        heating_circuits=(1,), hot_water=(1,), buffers=(1,), circulation_pump=True
    )

    with patch("custom_components.froeling_lambdatronic.config_flow.LambdatronicClient") as client_class:
        result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], MODBUS_INPUT))
    client_class.assert_not_called()
    assert result["step_id"] == "mismatch"
    result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], {}))

    assert result["reason"] == "reconfigure_successful"
    # The reload after the flow stored the keys of the Connect overview again.
    assert _without_cloud_keys(mock_config_entry.data) == entry_data(ConnectionType.HYBRID)


@pytest.mark.usefixtures("mock_client", "mock_cloud")
async def test_reconfigure_new_gateway(
    hass: HomeAssistant, mock_config_entry_modbus: MockConfigEntry
) -> None:
    """A new host changes the unique ID."""
    await setup_integration(hass, mock_config_entry_modbus)
    result = await _reconfigure(hass, mock_config_entry_modbus, "reconfigure_modbus")
    result = dict(
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.1.51", "advanced": {CONF_PORT: 502, CONF_SLAVE: 3}}
        )
    )

    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry_modbus.unique_id == "192.168.1.51:502:3"
    assert mock_config_entry_modbus.data == {
        **entry_data(ConnectionType.MODBUS),
        CONF_HOST: "192.168.1.51",
        CONF_SLAVE: 3,
    }
    await hass.async_block_till_done()
    assert mock_config_entry_modbus.state is ConfigEntryState.LOADED


@pytest.mark.usefixtures("mock_client", "mock_cloud")
async def test_reconfigure_to_other_entry(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    """Reconfiguring onto the connection of another entry is refused."""
    MockConfigEntry(domain=DOMAIN, version=2, unique_id="192.168.1.51:502:2", data=entry_data()).add_to_hass(
        hass
    )
    await setup_integration(hass, mock_config_entry)
    result = await _reconfigure(hass, mock_config_entry, "reconfigure_modbus")
    result = dict(
        await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: "192.168.1.51", "advanced": {CONF_PORT: 502, CONF_SLAVE: 2}}
        )
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reconfigure_add_modbus(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_cloud: MagicMock,
    mock_config_entry_cloud: MockConfigEntry,
) -> None:
    """Connect only + Modbus = hybrid; the unique ID becomes the Modbus connection."""
    await setup_integration(hass, mock_config_entry_cloud)
    result = await _reconfigure(hass, mock_config_entry_cloud, "reconfigure_modbus")
    mock_cloud.get_components.side_effect = ConnectionFailedError("down")
    result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], MODBUS_INPUT))
    assert result["errors"] == {"base": "cloud_cannot_connect"}

    mock_cloud.get_components.side_effect = None
    result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], MODBUS_INPUT))

    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry_cloud.unique_id == UNIQUE_ID
    # The reload after the flow stored the keys of the Connect overview again.
    assert _without_cloud_keys(mock_config_entry_cloud.data) == entry_data(ConnectionType.HYBRID)


async def test_reconfigure_add_cloud(
    hass: HomeAssistant,
    mock_client: MagicMock,
    mock_cloud: MagicMock,
    mock_config_entry_modbus: MockConfigEntry,
) -> None:
    """Modbus only + Connect = hybrid; Modbus is detected again with the running client."""
    await setup_integration(hass, mock_config_entry_modbus)
    detections = mock_client.detect_equipment.await_count
    result = await _reconfigure(hass, mock_config_entry_modbus, "reconfigure_cloud")
    assert result["step_id"] == "reconfigure_cloud"

    result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], CLOUD_INPUT))

    assert result["reason"] == "reconfigure_successful"
    # The reload after the flow stored the keys of the Connect overview again.
    assert _without_cloud_keys(mock_config_entry_modbus.data) == entry_data(ConnectionType.HYBRID)
    assert mock_config_entry_modbus.unique_id == UNIQUE_ID
    assert mock_client.detect_equipment.await_count == detections + 1


@pytest.mark.usefixtures("mock_client")
async def test_reconfigure_cloud_keeps_password(
    hass: HomeAssistant, mock_cloud: MagicMock, mock_config_entry_cloud: MockConfigEntry
) -> None:
    """The same username without a password keeps the stored password."""
    await setup_integration(hass, mock_config_entry_cloud)
    result = await _reconfigure(hass, mock_config_entry_cloud, "reconfigure_cloud")

    with patch(
        "custom_components.froeling_lambdatronic.config_flow.FroelingConnectClient", return_value=mock_cloud
    ) as client_class:
        result = dict(
            await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_USERNAME: USERNAME})
        )

    assert client_class.call_args.args[1:] == (USERNAME, PASSWORD)
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry_cloud.data == entry_data(ConnectionType.CLOUD)


@pytest.mark.usefixtures("mock_cloud")
async def test_reconfigure_remove_cloud(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Hybrid without Connect: Modbus only; a failed detection can be retried."""
    await setup_integration(hass, mock_config_entry)
    result = await _reconfigure(hass, mock_config_entry, "remove_cloud")
    assert result["step_id"] == "remove_cloud"
    mock_client.probe.side_effect = NoResponseError("timeout")
    result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], {}))
    assert result["errors"] == {"base": "no_response"}

    mock_client.probe.side_effect = None
    result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], {}))

    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data == entry_data(ConnectionType.MODBUS)
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.LOADED


async def test_reconfigure_remove_modbus(
    hass: HomeAssistant, mock_client: MagicMock, mock_cloud: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Hybrid without Modbus: Connect only with the facility as unique ID."""
    await setup_integration(hass, mock_config_entry)
    result = await _reconfigure(hass, mock_config_entry, "remove_modbus")
    assert result["step_id"] == "remove_modbus"
    mock_cloud.get_components.side_effect = ConnectionFailedError("down")
    result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], {}))
    assert result["errors"] == {"base": "cloud_cannot_connect"}

    mock_cloud.get_components.side_effect = None
    result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], {}))

    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data == entry_data(ConnectionType.CLOUD)
    assert mock_config_entry.unique_id == CLOUD_UNIQUE_ID
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.LOADED
    mock_client.close.assert_awaited()


@pytest.mark.usefixtures("mock_client", "mock_cloud")
async def test_reconfigure_remove_modbus_already_configured(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """Another entry with the same facility as unique ID blocks removing Modbus."""
    MockConfigEntry(domain=DOMAIN, version=2, unique_id=CLOUD_UNIQUE_ID, data={}).add_to_hass(hass)
    await setup_integration(hass, mock_config_entry)
    result = await _reconfigure(hass, mock_config_entry, "remove_modbus")
    result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], {}))
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


OPTIONS = {
    ConnectionType.MODBUS: {CONF_SCAN_INTERVAL: 60, CONF_PARAMETER_INTERVAL: 10},
    ConnectionType.CLOUD: {CONF_CLOUD_INTERVAL: 5},
    ConnectionType.HYBRID: {CONF_SCAN_INTERVAL: 60, CONF_PARAMETER_INTERVAL: 10, CONF_CLOUD_INTERVAL: 5},
}


@pytest.mark.usefixtures("mock_client", "mock_cloud")
async def test_options(hass: HomeAssistant, any_config_entry: MockConfigEntry) -> None:
    """Only the intervals of the used sources; saving reloads the entry."""
    user_input = OPTIONS[connection_type(any_config_entry.data)]
    await setup_integration(hass, any_config_entry)
    result: dict[str, Any] = dict(await hass.config_entries.options.async_init(any_config_entry.entry_id))
    assert result["step_id"] == "init"
    assert list(result["data_schema"].schema) == list(user_input)

    result = dict(await hass.config_entries.options.async_configure(result["flow_id"], user_input))
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert any_config_entry.options == user_input
    assert any_config_entry.state is ConfigEntryState.LOADED


@pytest.mark.parametrize(
    ("fixture", "option", "user_input", "expected"),
    [
        ("mock_config_entry", "reconfigure_modbus", MODBUS_INPUT, [1]),
        ("mock_config_entry", "reconfigure_cloud", CLOUD_INPUT, [1]),
        ("mock_config_entry_modbus", "reconfigure_cloud", CLOUD_INPUT, [1]),
        ("mock_config_entry", "remove_modbus", {}, [1]),
        ("mock_config_entry", "remove_cloud", {}, []),
    ],
)
@pytest.mark.usefixtures("mock_client")
async def test_reconfigure_room_sensors(
    hass: HomeAssistant,
    request: pytest.FixtureRequest,
    mock_cloud: MagicMock,
    fixture: str,
    option: str,
    user_input: dict[str, Any],
    expected: list[int],
) -> None:
    """Reconfiguring reads the room sensors again from Connect; without Connect there are none."""
    entry: MockConfigEntry = request.getfixturevalue(fixture)
    await setup_integration(hass, entry)
    hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_ROOM_SENSORS: [2]})
    mock_cloud.get_components.return_value = cloud_components((1, 2))
    mock_cloud.detail_room_sensors = {"hc1": True, "hc2": False}
    # The reload after the flow must not read Connect: the stored value is the one of the flow.
    mock_cloud.get_overview.side_effect = ConnectionFailedError("down")
    result = await _reconfigure(hass, entry, option)

    result = dict(await hass.config_entries.flow.async_configure(result["flow_id"], user_input))

    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_ROOM_SENSORS] == expected
