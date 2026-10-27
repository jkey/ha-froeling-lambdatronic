"""Fixtures for the Fröling Lambdatronic tests."""

from collections.abc import Generator, Sequence
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from froeling_lambdatronic import CloudComponentDetail, CloudOverview, CloudSnapshot, Facility, build_snapshot
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.froeling_lambdatronic.const import DOMAIN, ConnectionType

from .const import (
    CLOUD_COMPONENTS,
    CLOUD_DETAILS_BY_ID,
    CLOUD_ERRORS,
    CLOUD_OVERVIEW,
    CLOUD_STATE,
    CLOUD_UNIQUE_ID,
    ENTRY_ID,
    ENTRY_OPTIONS,
    EQUIPMENT,
    FACILITY_ID,
    HEADER,
    PARAMETERS,
    SYSTEM_INFO,
    UNIQUE_ID,
    VALUES,
    entry_data,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load the integration from custom_components."""


@pytest.fixture
def entity_registry_enabled_by_default() -> Generator[None]:
    """Enable entities that are disabled by default."""
    with patch(
        "homeassistant.helpers.entity.Entity.entity_registry_enabled_default",
        return_value=True,
    ):
        yield


def _entry(connection: ConnectionType) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        version=2,
        entry_id=ENTRY_ID,
        title="Fröling PE1",
        unique_id=CLOUD_UNIQUE_ID if connection is ConnectionType.CLOUD else UNIQUE_ID,
        data=entry_data(connection),
        options=ENTRY_OPTIONS,
    )


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Config entry with Modbus and Fröling Connect (hybrid)."""
    return _entry(ConnectionType.HYBRID)


@pytest.fixture
def mock_config_entry_modbus() -> MockConfigEntry:
    """Config entry with Modbus only."""
    return _entry(ConnectionType.MODBUS)


@pytest.fixture
def mock_config_entry_cloud() -> MockConfigEntry:
    """Config entry with Fröling Connect only."""
    return _entry(ConnectionType.CLOUD)


@pytest.fixture(params=list(ConnectionType))
def any_config_entry(request: pytest.FixtureRequest) -> MockConfigEntry:
    """Config entry of every connection type."""
    return _entry(request.param)


@pytest.fixture
def mock_client() -> Generator[MagicMock]:
    """LambdatronicClient mocked in setup and config flow."""
    client = MagicMock()
    client.connect = AsyncMock()
    client.close = AsyncMock()
    client.probe = AsyncMock(return_value=2)
    client.detect_equipment = AsyncMock(return_value=EQUIPMENT)
    client.read_values = AsyncMock(return_value=VALUES)
    client.read_parameters = AsyncMock(return_value=PARAMETERS)
    client.write = AsyncMock(side_effect=lambda register, value: value)
    with (
        patch("custom_components.froeling_lambdatronic.LambdatronicClient", return_value=client),
        patch("custom_components.froeling_lambdatronic.config_flow.LambdatronicClient", return_value=client),
    ):
        yield client


@pytest.fixture
def mock_cloud() -> Generator[MagicMock]:
    """FroelingConnectClient mocked in setup and config flow, answering with the cloud fixtures."""
    cloud = MagicMock()
    cloud.login = AsyncMock()
    cloud.get_facilities = AsyncMock(
        return_value=[Facility(FACILITY_ID, "Home", "PE1 Pellet 20", "100000001")]
    )
    cloud.get_components = AsyncMock(return_value=list(CLOUD_COMPONENTS))
    cloud.get_overview = AsyncMock(return_value=CLOUD_OVERVIEW)
    cloud.get_component_detail = AsyncMock(
        side_effect=lambda facility_id, component_id: CLOUD_DETAILS_BY_ID[component_id]
    )
    cloud.get_boiler_state = AsyncMock(return_value=CLOUD_STATE)
    cloud.get_errors = AsyncMock(return_value=list(CLOUD_ERRORS))
    cloud.get_system_info = AsyncMock(return_value=SYSTEM_INFO)
    cloud.get_header = AsyncMock(return_value=HEADER)
    cloud.set_value = AsyncMock()
    cloud.set_operating_mode = AsyncMock()
    # Tests change cloud values with `mock_cloud.snapshot_changes` (None removes a key).
    cloud.snapshot_changes = {}

    def snapshot(overview: CloudOverview, details: Sequence[CloudComponentDetail] = ()) -> CloudSnapshot:
        built = build_snapshot(overview, details)
        values = {**built.values, **cloud.snapshot_changes}
        return replace(built, values={key: value for key, value in values.items() if value is not None})

    with (
        patch("custom_components.froeling_lambdatronic.coordinator.build_snapshot", side_effect=snapshot),
        patch("custom_components.froeling_lambdatronic.FroelingConnectClient", return_value=cloud),
        patch(
            "custom_components.froeling_lambdatronic.config_flow.FroelingConnectClient", return_value=cloud
        ),
    ):
        yield cloud
