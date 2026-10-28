"""Tests for the schedule actions."""

from dataclasses import replace
from typing import Any
from unittest.mock import MagicMock

import probatio
import pytest
from froeling_lambdatronic import (
    AuthenticationError,
    CloudComponentDetail,
    CloudParameterNotFoundError,
    ConnectionFailedError,
    Phase,
    RemoteControlDisabledError,
    Weekday,
    WriteRejectedError,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry
from syrupy.assertion import SnapshotAssertion

from custom_components.froeling_lambdatronic.const import DOMAIN
from custom_components.froeling_lambdatronic.services import _end_minutes

from . import setup_integration
from .const import CLOUD_DETAILS_BY_ID, FACILITY_ID

HC1 = "climate.froling_pe1_wohnbereich"
DHW1 = "water_heater.froling_pe1_brauchwasser"


async def _get(hass: HomeAssistant, entity_id: str) -> Any:
    return await hass.services.async_call(
        DOMAIN, "get_schedule", {ATTR_ENTITY_ID: entity_id}, blocking=True, return_response=True
    )


async def _set(hass: HomeAssistant, entity_id: str, **days: object) -> None:
    await hass.services.async_call(DOMAIN, "set_schedule", {ATTR_ENTITY_ID: entity_id, **days}, blocking=True)


@pytest.mark.usefixtures("mock_client")
async def test_get_schedule(
    hass: HomeAssistant,
    mock_cloud: MagicMock,
    mock_config_entry: MockConfigEntry,
    snapshot: SnapshotAssertion,
) -> None:
    """The schedule of a heating circuit and a hot water tank, read from Fröling Connect."""
    await setup_integration(hass, mock_config_entry)

    mock_cloud.get_component_detail.reset_mock()

    assert await _get(hass, HC1) == snapshot(name="hc1")
    assert await _get(hass, DHW1) == snapshot(name="dhw1")
    assert [call.args for call in mock_cloud.get_component_detail.await_args_list] == [
        (FACILITY_ID, "300_3100"),
        (FACILITY_ID, "200_2100"),
    ]


def _with_unreadable_days(mock_cloud: MagicMock, *days: Weekday) -> None:
    """Component details whose phases on the given days could not be read."""

    def detail(facility_id: int, component_id: str) -> CloudComponentDetail:
        return replace(CLOUD_DETAILS_BY_ID[component_id], skipped_phase_days=frozenset(days))

    mock_cloud.get_component_detail.side_effect = detail


@pytest.mark.usefixtures("mock_client")
async def test_get_schedule_unreadable_days(
    hass: HomeAssistant, mock_cloud: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Days with unreadable phases are listed, so that a read-edit-write cycle keeps them."""
    await setup_integration(hass, mock_config_entry)
    _with_unreadable_days(mock_cloud, Weekday.FRIDAY, Weekday.TUESDAY)

    response = await _get(hass, HC1)

    assert response[HC1]["unreadable_days"] == ["tuesday", "friday"]


@pytest.mark.usefixtures("mock_client")
async def test_set_schedule(
    hass: HomeAssistant, mock_cloud: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Given days are replaced (sorted, 24:00 allowed, empty clears); the cloud is read again."""
    await setup_integration(hass, mock_config_entry)
    reads = mock_cloud.get_overview.await_count

    await _set(
        hass,
        HC1,
        monday=[{"from": "17:00", "to": "24:00"}, {"from": "05:30:00", "to": "08:00"}],
        sunday=[],
    )

    mock_cloud.set_schedule.assert_awaited_once_with(
        FACILITY_ID,
        "hc1",
        {Weekday.MONDAY: [Phase(330, 480), Phase(1020, 1440)], Weekday.SUNDAY: []},
    )
    assert mock_cloud.get_overview.await_count == reads + 1


@pytest.mark.parametrize(
    "monday",
    [
        [{"from": f"{hour:02d}:00", "to": f"{hour:02d}:30"} for hour in range(5)],
        [{"from": "06:00", "to": "09:00"}, {"from": "08:00", "to": "10:00"}],
        [{"from": "22:00", "to": "06:00"}],
    ],
    ids=["five_phases", "overlap", "end_before_start"],
)
@pytest.mark.usefixtures("mock_client")
async def test_set_schedule_invalid(
    hass: HomeAssistant,
    mock_cloud: MagicMock,
    mock_config_entry: MockConfigEntry,
    monday: list[dict[str, str]],
) -> None:
    """Invalid days are refused before anything is sent."""
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(ServiceValidationError) as raised:
        await _set(hass, HC1, monday=monday)

    assert raised.value.translation_key == "invalid_schedule"
    assert raised.value.translation_placeholders is not None
    assert raised.value.translation_placeholders["day"] == "monday"
    mock_cloud.set_schedule.assert_not_awaited()


def test_end_of_day() -> None:
    """'24:00' is the end of the day; other times are parsed as usual."""
    assert _end_minutes("24:00") == 1440
    assert _end_minutes("24:00:00") == 1440
    assert _end_minutes("23:59") == 1439
    with pytest.raises(probatio.Invalid):
        _end_minutes("25:00")


@pytest.mark.usefixtures("mock_client")
async def test_requires_connect(hass: HomeAssistant, mock_config_entry_modbus: MockConfigEntry) -> None:
    """Without Fröling Connect there are no schedules."""
    await setup_integration(hass, mock_config_entry_modbus)

    for action in (
        _get(hass, "climate.froling_pe1_heating_circuit_01"),
        _set(hass, "climate.froling_pe1_heating_circuit_01", monday=[]),
    ):
        with pytest.raises(ServiceValidationError) as raised:
            await action
        assert raised.value.translation_key == "schedule_requires_connect"


@pytest.mark.parametrize(
    ("error", "translation_key"),
    [
        (RemoteControlDisabledError("off"), "remote_control_disabled"),
        (AuthenticationError("rejected"), "cloud_auth_failed"),
        (WriteRejectedError("hc1", "rejected"), "write_rejected"),
        (ConnectionFailedError("down"), "cloud_write_failed"),
    ],
)
@pytest.mark.usefixtures("mock_client")
async def test_set_schedule_errors(
    hass: HomeAssistant,
    mock_cloud: MagicMock,
    mock_config_entry: MockConfigEntry,
    error: Exception,
    translation_key: str,
) -> None:
    """Errors of Fröling Connect become translated errors."""
    await setup_integration(hass, mock_config_entry)
    mock_cloud.set_schedule.side_effect = error

    with pytest.raises(HomeAssistantError) as raised:
        await _set(hass, DHW1, monday=[{"from": "06:00", "to": "07:00"}])

    assert raised.value.translation_key == translation_key


@pytest.mark.usefixtures("mock_client")
async def test_get_schedule_error(
    hass: HomeAssistant, mock_cloud: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A failed read is reported."""
    await setup_integration(hass, mock_config_entry)
    mock_cloud.get_component_detail.side_effect = ConnectionFailedError("down")

    with pytest.raises(HomeAssistantError) as raised:
        await _get(hass, HC1)

    assert raised.value.translation_key == "cloud_update_failed"


@pytest.mark.usefixtures("mock_client")
async def test_set_schedule_without_days(
    hass: HomeAssistant, mock_cloud: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Without any day there is nothing to write."""
    await setup_integration(hass, mock_config_entry)

    with pytest.raises(ServiceValidationError) as raised:
        await _set(hass, HC1)

    assert raised.value.translation_key == "schedule_no_days"
    mock_cloud.set_schedule.assert_not_awaited()


@pytest.mark.usefixtures("mock_client")
async def test_set_schedule_unreadable_days(
    hass: HomeAssistant, mock_cloud: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """Kept days with unreadable phases must be given, otherwise writing would erase them."""
    await setup_integration(hass, mock_config_entry)
    _with_unreadable_days(mock_cloud, Weekday.TUESDAY, Weekday.FRIDAY)
    phase = [{"from": "06:00", "to": "07:00"}]

    with pytest.raises(ServiceValidationError) as raised:
        await _set(hass, HC1, monday=phase, tuesday=phase)

    assert raised.value.translation_key == "schedule_unreadable_days"
    assert raised.value.translation_placeholders == {"days": "friday"}
    mock_cloud.set_schedule.assert_not_awaited()

    await _set(hass, HC1, monday=phase, tuesday=phase, friday=[])

    mock_cloud.set_schedule.assert_awaited_once_with(
        FACILITY_ID,
        "hc1",
        {Weekday.MONDAY: [Phase(360, 420)], Weekday.TUESDAY: [Phase(360, 420)], Weekday.FRIDAY: []},
    )


@pytest.mark.usefixtures("mock_client")
async def test_no_weekly_schedule(
    hass: HomeAssistant, mock_cloud: MagicMock, mock_config_entry: MockConfigEntry
) -> None:
    """A component without a weekly schedule in Fröling Connect is reported as such."""
    await setup_integration(hass, mock_config_entry)
    mock_cloud.set_schedule.side_effect = CloudParameterNotFoundError("hc1 has no weekly schedule")

    with pytest.raises(ServiceValidationError) as raised:
        await _set(hass, HC1, monday=[])
    assert raised.value.translation_key == "no_schedule"

    mock_cloud.get_components.return_value = []
    with pytest.raises(ServiceValidationError) as raised:
        await _get(hass, HC1)
    assert raised.value.translation_key == "no_schedule"
