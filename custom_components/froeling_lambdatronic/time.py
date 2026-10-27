"""Time entities of the Fröling Lambdatronic integration."""

from datetime import time

from homeassistant.components.time import TimeEntity, TimeEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FroelingConfigEntry
from .entity import BOILER_COMPONENT, Component, FroelingWritableEntity
from .sources import FroelingSources, Source

PARALLEL_UPDATES = 1

BOILER_TIMES: tuple[TimeEntityDescription, ...] = (
    TimeEntityDescription(
        key="pellet_filling_start_1",
        translation_key="pellet_filling_start_1",
        entity_category=EntityCategory.CONFIG,
    ),
    # Read-only via Modbus (40095): only created if Fröling Connect can write it.
    TimeEntityDescription(
        key="pellet_filling_start_2",
        translation_key="pellet_filling_start_2",
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FroelingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a time entity for every time of day that can be read and written."""
    sources = entry.runtime_data.sources
    async_add_entities(
        FroelingTime(sources, BOILER_COMPONENT, description, source)
        for description in BOILER_TIMES
        if (source := sources.source(description.key)) is not None and sources.writable(description.key)
    )


class FroelingTime(FroelingWritableEntity, TimeEntity):
    """Time of day setting."""

    def __init__(
        self,
        sources: FroelingSources,
        component: Component,
        description: TimeEntityDescription,
        source: Source,
    ) -> None:
        super().__init__(sources, component, description.key, source)
        self.entity_description = description

    @property
    def native_value(self) -> time | None:
        """Current time of day."""
        value = self.flat_value
        return value if isinstance(value, time) else None

    async def async_set_value(self, value: time) -> None:
        """Write the time of day."""
        await self.async_write(value)
