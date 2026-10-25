"""Fixtures for the Fröling Lambdatronic tests."""

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from froeling_lambdatronic import Facility
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.froeling_lambdatronic.const import DOMAIN

from .const import (
    CLOUD_STATE,
    ENTRY_ID,
    ENTRY_OPTIONS,
    EQUIPMENT,
    FACILITY_ID,
    PARAMETERS,
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


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Config entry with Fröling Connect."""
    return MockConfigEntry(
        domain=DOMAIN,
        entry_id=ENTRY_ID,
        title="Fröling PE1",
        unique_id=UNIQUE_ID,
        data=entry_data(),
        options=ENTRY_OPTIONS,
    )


@pytest.fixture
def mock_config_entry_local() -> MockConfigEntry:
    """Config entry without Fröling Connect."""
    return MockConfigEntry(
        domain=DOMAIN,
        entry_id=ENTRY_ID,
        title="Fröling PE1",
        unique_id=UNIQUE_ID,
        data=entry_data(cloud=False),
        options=ENTRY_OPTIONS,
    )


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
    """FroelingConnectClient mocked in setup and config flow."""
    cloud = MagicMock()
    cloud.login = AsyncMock()
    cloud.get_facilities = AsyncMock(return_value=[Facility(FACILITY_ID, "Haus", "PE1")])
    cloud.get_boiler_state = AsyncMock(return_value=CLOUD_STATE)
    cloud.set_operating_mode = AsyncMock()
    with (
        patch("custom_components.froeling_lambdatronic.FroelingConnectClient", return_value=cloud),
        patch(
            "custom_components.froeling_lambdatronic.config_flow.FroelingConnectClient", return_value=cloud
        ),
    ):
        yield cloud
