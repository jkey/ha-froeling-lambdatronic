"""Button entities of the Fröling Lambdatronic integration."""

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FroelingConfigEntry, ParametersCoordinator
from .entity import BOILER_COMPONENT, FroelingEntity

PARALLEL_UPDATES = 1

REFRESH_PARAMETERS = ButtonEntityDescription(
    key="refresh_parameters",
    translation_key="refresh_parameters",
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FroelingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the button."""
    async_add_entities([FroelingRefreshButton(entry.runtime_data.parameters)])


class FroelingRefreshButton(FroelingEntity[ParametersCoordinator], ButtonEntity):
    """Read all parameters again (e.g. after a change at the boiler display)."""

    entity_description = REFRESH_PARAMETERS

    def __init__(self, coordinator: ParametersCoordinator) -> None:
        super().__init__(coordinator, BOILER_COMPONENT, REFRESH_PARAMETERS.key)

    async def async_press(self) -> None:
        """Refresh the parameters now."""
        await self.coordinator.async_refresh()
