"""Tests for the config, reconfigure, reauth and options flows."""

from unittest.mock import MagicMock, patch

import pytest
from froeling_lambdatronic import (
    AuthenticationError,
    CloudBoilerState,
    CommunicationError,
    ConnectionFailedError,
    Equipment,
    Facility,
    NoResponseError,
    OperatingMode,
)
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.froeling_lambdatronic.const import (
    CONF_BUFFERS,
    CONF_CLOUD,
    CONF_EQUIPMENT,
    CONF_FACILITY_ID,
    CONF_HEATING_CIRCUITS,
    CONF_HOT_WATER,
    CONF_PARAMETER_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_SLAVE,
    DOMAIN,
)

from . import setup_integration
from .const import ENTRY_OPTIONS, FACILITY_ID, HOST, PASSWORD, UNIQUE_ID, USERNAME, entry_data

USER_INPUT = {CONF_HOST: HOST, CONF_PORT: 502, CONF_SLAVE: 2, CONF_NAME: "Fröling PE1"}
EQUIPMENT_INPUT = {CONF_HEATING_CIRCUITS: ["1", "2"], CONF_HOT_WATER: ["1"], CONF_BUFFERS: ["1"]}


async def _to_cloud_step(hass: HomeAssistant) -> str:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "equipment"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], EQUIPMENT_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "cloud"
    return result["flow_id"]


@pytest.mark.usefixtures("mock_client", "mock_cloud")
async def test_user_flow_with_cloud(hass: HomeAssistant) -> None:
    """Full setup with Fröling Connect."""
    flow_id = await _to_cloud_step(hass)

    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Fröling PE1"
    assert result["data"] == entry_data()
    assert result["options"] == ENTRY_OPTIONS
    assert result["result"].unique_id == UNIQUE_ID


@pytest.mark.usefixtures("mock_client")
async def test_user_flow_without_cloud(hass: HomeAssistant) -> None:
    """An empty username skips Fröling Connect; deselected components are stored in the options."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**EQUIPMENT_INPUT, CONF_HEATING_CIRCUITS: ["2"]}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == entry_data(cloud=False)
    assert result["options"] == {**ENTRY_OPTIONS, CONF_HEATING_CIRCUITS: [2]}


@pytest.mark.usefixtures("mock_client")
async def test_user_flow_multiple_facilities(hass: HomeAssistant, mock_cloud: MagicMock) -> None:
    """With several facilities the user picks one."""
    mock_cloud.get_facilities.return_value = [
        Facility(1, "Ferienhaus", "PE1"),
        Facility(FACILITY_ID, "", "PE1"),
    ]
    flow_id = await _to_cloud_step(hass)

    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "facility"

    for error, expected in (
        (ConnectionFailedError("down"), "cloud_cannot_connect"),
        (RuntimeError("boom"), "unknown"),
    ):
        mock_cloud.get_boiler_state.side_effect = error
        result = await hass.config_entries.flow.async_configure(flow_id, {CONF_FACILITY_ID: str(FACILITY_ID)})
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": expected}

    mock_cloud.get_boiler_state.side_effect = None
    result = await hass.config_entries.flow.async_configure(flow_id, {CONF_FACILITY_ID: str(FACILITY_ID)})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CLOUD][CONF_FACILITY_ID] == FACILITY_ID


@pytest.mark.usefixtures("mock_client")
async def test_user_flow_remote_control_disabled(
    hass: HomeAssistant, mock_cloud: MagicMock, issue_registry: ir.IssueRegistry
) -> None:
    """Remote control switched off does not block the setup; a repair issue tells the user."""
    mock_cloud.get_boiler_state.return_value = CloudBoilerState(OperatingMode.AUTOMATIC, False)
    flow_id = await _to_cloud_step(hass)

    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == entry_data()
    await hass.async_block_till_done()

    entry = result["result"]
    assert entry.state is ConfigEntryState.LOADED
    assert issue_registry.async_get_issue(DOMAIN, f"remote_control_disabled_{entry.entry_id}") is not None


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (NoResponseError("timeout"), "no_response"),
        (ConnectionFailedError("refused"), "cannot_connect"),
        (CommunicationError("exception 0x0B"), "invalid_slave"),
        (RuntimeError("boom"), "unknown"),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant, mock_client: MagicMock, error: Exception, expected: str
) -> None:
    """Connection errors are shown and the user can try again."""
    mock_client.probe.side_effect = error
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}
    mock_client.close.assert_awaited()

    mock_client.probe.side_effect = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "equipment"


@pytest.mark.parametrize(
    ("method", "side_effect", "expected"),
    [
        ("login", AuthenticationError("rejected"), "invalid_auth"),
        ("get_facilities", ConnectionFailedError("down"), "cloud_cannot_connect"),
        ("get_facilities", [[]], "no_facilities"),
        ("login", RuntimeError("boom"), "unknown"),
        ("get_boiler_state", RuntimeError("boom"), "unknown"),
    ],
)
@pytest.mark.usefixtures("mock_client")
async def test_cloud_errors(
    hass: HomeAssistant, mock_cloud: MagicMock, method: str, side_effect: object, expected: str
) -> None:
    """Fröling Connect errors are shown; afterwards the login succeeds."""
    flow_id = await _to_cloud_step(hass)
    getattr(mock_cloud, method).side_effect = side_effect

    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": expected}

    getattr(mock_cloud, method).side_effect = None
    result = await hass.config_entries.flow.async_configure(
        flow_id, {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.usefixtures("mock_client")
async def test_already_configured(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    """The same gateway and slave cannot be added twice."""
    mock_config_entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.usefixtures("mock_client")
async def test_reauth(hass: HomeAssistant, mock_cloud: MagicMock, mock_config_entry: MockConfigEntry) -> None:
    """New Fröling Connect credentials are stored and the entry reloads."""
    await setup_integration(hass, mock_config_entry)
    result = await mock_config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    mock_cloud.login.side_effect = AuthenticationError("rejected")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: USERNAME, CONF_PASSWORD: "falsch"}
    )
    assert result["errors"] == {"base": "invalid_auth"}

    mock_cloud.login.side_effect = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_USERNAME: USERNAME, CONF_PASSWORD: "neu"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert mock_config_entry.data[CONF_CLOUD] == {
        CONF_USERNAME: USERNAME,
        CONF_PASSWORD: "neu",
        CONF_FACILITY_ID: FACILITY_ID,
    }


@pytest.mark.usefixtures("mock_cloud")
async def test_reconfigure_same_gateway_reuses_client(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Unchanged connection: the running client is used, the stored password kept."""
    await setup_integration(hass, mock_config_entry)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"

    with patch("custom_components.froeling_lambdatronic.config_flow.LambdatronicClient") as client_class:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_HOST: HOST, CONF_PORT: 502, CONF_SLAVE: 2}
        )
    client_class.assert_not_called()
    mock_client.detect_equipment.assert_awaited()
    assert result["step_id"] == "cloud"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_USERNAME: USERNAME})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data == entry_data()


@pytest.mark.usefixtures("mock_client", "mock_cloud")
async def test_reconfigure_new_gateway_without_cloud(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A new host changes the unique ID; an empty username removes Fröling Connect."""
    await setup_integration(hass, mock_config_entry)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.70.19", CONF_PORT: 502, CONF_SLAVE: 3}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.unique_id == "192.168.70.19:502:3"
    assert mock_config_entry.data == {
        **entry_data(cloud=False),
        CONF_HOST: "192.168.70.19",
        CONF_SLAVE: 3,
    }
    await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.LOADED


@pytest.mark.usefixtures("mock_client", "mock_cloud")
async def test_reconfigure_to_other_entry(hass: HomeAssistant, mock_config_entry: MockConfigEntry) -> None:
    """Reconfiguring onto the connection of another entry is refused."""
    MockConfigEntry(domain=DOMAIN, unique_id="192.168.70.19:502:2", data=entry_data()).add_to_hass(hass)
    await setup_integration(hass, mock_config_entry)
    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.70.19", CONF_PORT: 502, CONF_SLAVE: 2}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.usefixtures("mock_client")
async def test_options(hass: HomeAssistant, mock_config_entry_local: MockConfigEntry) -> None:
    """Options change the components and intervals and reload the entry."""
    await setup_integration(hass, mock_config_entry_local)
    result = await hass.config_entries.options.async_init(mock_config_entry_local.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {**EQUIPMENT_INPUT, CONF_BUFFERS: [], CONF_SCAN_INTERVAL: 60, CONF_PARAMETER_INTERVAL: 10},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry_local.options == {
        **ENTRY_OPTIONS,
        CONF_BUFFERS: [],
        CONF_SCAN_INTERVAL: 60,
        CONF_PARAMETER_INTERVAL: 10,
    }
    assert mock_config_entry_local.state is ConfigEntryState.LOADED
    assert mock_config_entry_local.runtime_data.equipment.buffers == ()
    assert mock_config_entry_local.data[CONF_EQUIPMENT][CONF_BUFFERS] == [1]


async def test_user_flow_partial_equipment(hass: HomeAssistant, mock_client: MagicMock) -> None:
    """Components that were not detected get no selection field and stay empty."""
    mock_client.detect_equipment.return_value = Equipment(heating_circuits=(1,), hot_water=(), buffers=())
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["step_id"] == "equipment"
    schema = result["data_schema"]
    assert schema is not None
    assert list(schema.schema) == [CONF_HEATING_CIRCUITS]

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_HEATING_CIRCUITS: ["1"]})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_EQUIPMENT] == {
        CONF_HEATING_CIRCUITS: [1],
        CONF_HOT_WATER: [],
        CONF_BUFFERS: [],
    }
    assert result["options"][CONF_HOT_WATER] == []


@pytest.mark.usefixtures("mock_cloud")
async def test_reconfigure_same_gateway_merges_equipment(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Same boiler: a briefly missed component is kept, a new one is stored and enabled."""
    mock_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry, options={**ENTRY_OPTIONS, CONF_HEATING_CIRCUITS: [2]}
    )
    await hass.config_entries.async_setup(mock_config_entry.entry_id)
    await hass.async_block_till_done()
    mock_client.detect_equipment.return_value = Equipment(heating_circuits=(3, 1), hot_water=(1,), buffers=())

    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: HOST, CONF_PORT: 502, CONF_SLAVE: 2}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_USERNAME: USERNAME})
    await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data[CONF_EQUIPMENT] == {
        CONF_HEATING_CIRCUITS: [1, 2, 3],
        CONF_HOT_WATER: [1],
        CONF_BUFFERS: [1],
    }
    assert mock_config_entry.options == {**ENTRY_OPTIONS, CONF_HEATING_CIRCUITS: [2, 3]}


@pytest.mark.usefixtures("mock_cloud")
async def test_reconfigure_new_gateway_replaces_equipment(
    hass: HomeAssistant, mock_client: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Other boiler: the new detection replaces the equipment; new components are enabled."""
    await setup_integration(hass, mock_config_entry)
    mock_client.detect_equipment.return_value = Equipment(heating_circuits=(1,), hot_water=(), buffers=(2,))

    result = await mock_config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_HOST: "192.168.70.19", CONF_PORT: 502, CONF_SLAVE: 2}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_USERNAME: USERNAME})
    await hass.async_block_till_done()

    assert result["reason"] == "reconfigure_successful"
    assert mock_config_entry.data[CONF_EQUIPMENT] == {
        CONF_HEATING_CIRCUITS: [1],
        CONF_HOT_WATER: [],
        CONF_BUFFERS: [2],
    }
    assert mock_config_entry.options == {
        **ENTRY_OPTIONS,
        CONF_HEATING_CIRCUITS: [1],
        CONF_HOT_WATER: [],
        CONF_BUFFERS: [2],
    }


@pytest.mark.usefixtures("mock_client")
async def test_options_ignore_stale_selection(
    hass: HomeAssistant, mock_config_entry_local: MockConfigEntry
) -> None:
    """Selected numbers that are no longer stored are not preselected and do not break the form."""
    mock_config_entry_local.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        mock_config_entry_local, options={**ENTRY_OPTIONS, CONF_HEATING_CIRCUITS: [1, 5]}
    )
    await hass.config_entries.async_setup(mock_config_entry_local.entry_id)
    await hass.async_block_till_done()

    result = await hass.config_entries.options.async_init(mock_config_entry_local.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 30, CONF_PARAMETER_INTERVAL: 5}
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert mock_config_entry_local.options == {**ENTRY_OPTIONS, CONF_HEATING_CIRCUITS: [1]}
