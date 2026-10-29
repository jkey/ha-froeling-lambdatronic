"""Actions of the Fröling Lambdatronic integration: read and write weekly schedules via Fröling Connect."""

from collections.abc import Collection
from functools import partial
from typing import Any

import probatio
from froeling_lambdatronic import Phase, Weekday, time_to_minutes, validate_day
from homeassistant.components.climate import DOMAIN as CLIMATE_DOMAIN
from homeassistant.components.water_heater import DOMAIN as WATER_HEATER_DOMAIN
from homeassistant.const import ATTR_ENTITY_ID, ENTITY_MATCH_ALL
from homeassistant.core import (
    EntityServiceResponse,
    HassJob,
    HassJobType,
    HomeAssistant,
    ServiceCall,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import async_get_platforms
from homeassistant.helpers.service import entity_service_call
from homeassistant.helpers.target import TargetSelection, async_extract_referenced_entity_ids
from homeassistant.util.json import JsonObjectType, JsonValueType

from .const import DOMAIN
from .entity import FroelingEntity

SERVICE_GET_SCHEDULE = "get_schedule"
SERVICE_SET_SCHEDULE = "set_schedule"
ATTR_FROM = "from"
ATTR_TO = "to"
ATTR_UNREADABLE_DAYS = "unreadable_days"
DAYS = {day.lower(): day for day in Weekday}
END_OF_DAY = "24:00"
MINUTES_PER_DAY = 24 * 60
# Schedules of heating circuits (climate) and hot water tanks (water_heater).
SCHEDULE_DOMAINS = (CLIMATE_DOMAIN, WATER_HEATER_DOMAIN)


def _end_minutes(value: Any) -> int:  # noqa: ANN401 - validator of untyped service data
    """End of a phase in minutes; '24:00' is the end of the day."""
    if isinstance(value, str) and value in (END_OF_DAY, f"{END_OF_DAY}:00"):
        return MINUTES_PER_DAY
    return time_to_minutes(cv.time(value))


SLOT_SCHEMA = probatio.Schema(
    {
        probatio.Required(ATTR_FROM): probatio.All(cv.time, time_to_minutes),
        probatio.Required(ATTR_TO): _end_minutes,
    }
)
SET_SCHEDULE_SCHEMA = cv.make_entity_service_schema(
    {probatio.Optional(day): probatio.All(cv.ensure_list, [SLOT_SCHEMA]) for day in DAYS}
)


def _text(minutes: int) -> str:
    """'HH:MM'; 1440 minutes is '24:00'."""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _days(weekdays: Collection[Weekday]) -> list[str]:
    """Days in week order, spelled as the service fields ('monday')."""
    return [day for day, weekday in DAYS.items() if weekday in weekdays]


async def async_get_schedule(entity: FroelingEntity, call: ServiceCall) -> JsonObjectType:
    """Weekly schedule of the entity's component, e.g. {'monday': [{'from': '04:30', 'to': '22:00'}]}.

    `unreadable_days` lists the days whose phases could not be read: they are missing in the
    response and must be given again when the schedule is written.
    """
    detail = await entity.sources.async_get_schedule(entity.component.key)
    response: JsonObjectType = {
        day: [
            {ATTR_FROM: _text(phase.start), ATTR_TO: _text(phase.end)}
            for phase in detail.schedule.get(weekday, ())
        ]
        for day, weekday in DAYS.items()
    }
    response[ATTR_UNREADABLE_DAYS] = list[JsonValueType](_days(detail.skipped_phase_days))
    return response


async def async_set_schedule(entity: FroelingEntity, call: ServiceCall) -> None:
    """Replace the phases of the given days; missing days stay, an empty list clears a day."""
    changes: dict[Weekday, list[Phase]] = {}
    for day, weekday in DAYS.items():
        if (slots := call.data.get(day)) is None:
            continue
        try:
            phases = sorted(Phase(slot[ATTR_FROM], slot[ATTR_TO]) for slot in slots)
            validate_day(phases)
        except ValueError as err:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_schedule",
                translation_placeholders={"day": day, "error": str(err)},
            ) from err
        changes[weekday] = phases
    if not changes:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="schedule_no_days")
    # Writing sends the whole week: a kept day whose phases could not be read would be erased.
    detail = await entity.sources.async_get_schedule(entity.component.key)
    if missing := _days(detail.skipped_phase_days - changes.keys()):
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="schedule_unreadable_days",
            translation_placeholders={"days": ", ".join(missing)},
        )
    await entity.sources.async_set_schedule(entity.component.key, changes)


@callback
def _schedule_entities(hass: HomeAssistant) -> dict[str, Entity]:
    """Climate and water heater entities of this integration."""
    entities: dict[str, Entity] = {}
    for platform in async_get_platforms(hass, DOMAIN):
        if platform.domain in SCHEDULE_DOMAINS:
            entities.update(platform.entities)
    return entities


async def _async_entity_call(
    hass: HomeAssistant, job: HassJob[..., Any], call: ServiceCall
) -> EntityServiceResponse | None:
    """Call the action on the targeted entities; an unavailable one named explicitly is an error.

    Home Assistant skips unavailable entities with a log entry only (or a generic error when a
    response is asked for), which looks as if a schedule had been written.
    """
    entities = _schedule_entities(hass)
    if call.data.get(ATTR_ENTITY_ID) != ENTITY_MATCH_ALL:
        referenced = async_extract_referenced_entity_ids(hass, TargetSelection(call.data))
        if unavailable := sorted(
            entity_id
            for entity_id in referenced.referenced
            if (entity := entities.get(entity_id)) is not None and not entity.available
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="entity_unavailable",
                translation_placeholders={"entities": ", ".join(unavailable)},
            )
    return await entity_service_call(hass, entities, job, call)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the schedule actions for heating circuits and hot water tanks.

    One action serves two entity domains, which `async_register_platform_entity_service` (one
    domain per service) cannot express; the entities are resolved the same way.
    """
    hass.services.async_register(
        DOMAIN,
        SERVICE_GET_SCHEDULE,
        partial(_async_entity_call, hass, HassJob(async_get_schedule)),
        cv.make_entity_service_schema({}),
        SupportsResponse.ONLY,
        job_type=HassJobType.Coroutinefunction,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SCHEDULE,
        partial(_async_entity_call, hass, HassJob(async_set_schedule)),
        SET_SCHEDULE_SCHEMA,
        job_type=HassJobType.Coroutinefunction,
    )
