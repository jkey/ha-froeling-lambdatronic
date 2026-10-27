"""Button entities of the Fröling Lambdatronic integration."""

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import FroelingConfigEntry, ModbusParametersCoordinator
from .entity import BOILER_COMPONENT, FroelingEntity, FroelingWritableEntity
from .sources import FroelingSources, Source

PARALLEL_UPDATES = 1

REFRESH_PARAMETERS = ButtonEntityDescription(
    key="refresh_parameters",
    translation_key="refresh_parameters",
    entity_category=EntityCategory.DIAGNOSTIC,
)

# Actions of Fröling Connect, triggered by writing True.
RESET_BUTTONS: tuple[ButtonEntityDescription, ...] = (
    ButtonEntityDescription(
        key="reset_ash_counter", translation_key="reset_ash_counter", entity_category=EntityCategory.CONFIG
    ),
    ButtonEntityDescription(
        key="reset_pellet_counter",
        translation_key="reset_pellet_counter",
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FroelingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the buttons."""
    data = entry.runtime_data
    sources = data.sources
    entities: list[ButtonEntity] = []
    if data.parameters is not None:
        entities.append(FroelingRefreshButton(sources, data.parameters))
    entities += [
        FroelingResetButton(sources, description)
        for description in RESET_BUTTONS
        if sources.write_source(description.key) is Source.CLOUD
    ]
    async_add_entities(entities)


class FroelingRefreshButton(FroelingEntity, ButtonEntity):
    """Read all Modbus parameters again (e.g. after a change at the boiler display).

    Available whenever the boiler answers, so that failed parameter reads can be retried.
    """

    entity_description = REFRESH_PARAMETERS

    def __init__(self, sources: FroelingSources, parameters: ModbusParametersCoordinator) -> None:
        super().__init__(sources, BOILER_COMPONENT, REFRESH_PARAMETERS.key, Source.MODBUS_VALUES)
        self._parameters = parameters

    async def async_press(self) -> None:
        """Refresh the parameters now."""
        await self._parameters.async_refresh()
        if not self._parameters.last_update_success:
            error = self._parameters.last_exception
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="parameters_read_failed",
                # The cause is the library error; the UpdateFailed itself carries an English text.
                translation_placeholders={"error": str(error and (error.__cause__ or error))},
            )


class FroelingResetButton(FroelingWritableEntity, ButtonEntity):
    """Reset a counter via Fröling Connect."""

    def __init__(self, sources: FroelingSources, description: ButtonEntityDescription) -> None:
        super().__init__(sources, BOILER_COMPONENT, description.key, Source.CLOUD)
        self.entity_description = description

    async def async_press(self) -> None:
        """Trigger the reset."""
        await self.async_write(True)
