"""Switch entities of the Fröling Lambdatronic integration."""

from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FroelingConfigEntry
from .entity import BOILER_COMPONENT, Component, FroelingWritableEntity
from .sources import FroelingSources, Source

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class FroelingSwitchDescription(SwitchEntityDescription):
    """Switch writing an on/off value; `inverted` if the value means 'off' (e.g. 'auto_feeder_off')."""

    value_key: str
    inverted: bool = False


BOILER_SWITCHES: tuple[FroelingSwitchDescription, ...] = (
    # 'Kessel EIN/AUS' of Fröling Connect.
    FroelingSwitchDescription(key="boiler_on", translation_key="boiler_on", value_key="boiler_on"),
    FroelingSwitchDescription(
        key="auto_feeder",
        translation_key="auto_feeder",
        entity_category=EntityCategory.CONFIG,
        value_key="auto_feeder_off",
        inverted=True,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FroelingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a switch for every on/off setting that can be read and written."""
    sources = entry.runtime_data.sources
    async_add_entities(
        FroelingSwitch(sources, BOILER_COMPONENT, description, source)
        for description in BOILER_SWITCHES
        if (source := sources.source(description.value_key)) is not None
        and sources.writable(description.value_key)
    )


class FroelingSwitch(FroelingWritableEntity, SwitchEntity):
    """On/off setting."""

    entity_description: FroelingSwitchDescription

    def __init__(
        self,
        sources: FroelingSources,
        component: Component,
        description: FroelingSwitchDescription,
        source: Source,
    ) -> None:
        super().__init__(
            sources,
            component,
            description.key,
            source,
            value_key=component.key_prefix + description.value_key,
        )
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Current state."""
        value = self.flat_value
        return None if value is None else bool(value) != self.entity_description.inverted

    async def async_turn_on(self, **kwargs: Any) -> None:  # noqa: ANN401 - SwitchEntity signature
        """Switch on."""
        await self.async_write(not self.entity_description.inverted)

    async def async_turn_off(self, **kwargs: Any) -> None:  # noqa: ANN401 - SwitchEntity signature
        """Switch off."""
        await self.async_write(self.entity_description.inverted)
