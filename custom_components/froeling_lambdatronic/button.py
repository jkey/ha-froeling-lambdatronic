"""Button entities of the Fröling Lambdatronic integration."""

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import FroelingConfigEntry, ParametersCoordinator, ValuesCoordinator
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
    data = entry.runtime_data
    async_add_entities([FroelingRefreshButton(data.values, data.parameters)])


class FroelingRefreshButton(FroelingEntity[ValuesCoordinator], ButtonEntity):
    """Read all parameters again (e.g. after a change at the boiler display).

    Available whenever the boiler answers, so that failed parameter reads can be retried.
    """

    entity_description = REFRESH_PARAMETERS

    def __init__(self, coordinator: ValuesCoordinator, parameters: ParametersCoordinator) -> None:
        super().__init__(coordinator, BOILER_COMPONENT, REFRESH_PARAMETERS.key)
        self._parameters = parameters

    async def async_press(self) -> None:
        """Refresh the parameters now."""
        await self._parameters.async_refresh()
        if not self._parameters.last_update_success:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="parameters_read_failed",
                translation_placeholders={"error": str(self._parameters.last_exception)},
            )
