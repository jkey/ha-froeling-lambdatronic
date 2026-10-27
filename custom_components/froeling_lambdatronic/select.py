"""Select entities of the Fröling Lambdatronic integration."""

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import HEATING_CIRCUIT_MODE_KEYS, OPERATING_MODE_KEYS
from .coordinator import FroelingConfigEntry
from .entity import BOILER_COMPONENT, Component, ComponentKind, FroelingWritableEntity
from .sources import FroelingSources, Source

PARALLEL_UPDATES = 1

HEATING_CIRCUIT_MODE = SelectEntityDescription(
    key="operating_mode",
    translation_key="heating_circuit_mode",
    options=list(HEATING_CIRCUIT_MODE_KEYS.values()),
)

# Shown from 34001 (Modbus) or 'mode2' (cloud), switched via Fröling Connect.
SYSTEM_MODE = SelectEntityDescription(
    key="system_operating_mode",
    translation_key="system_operating_mode",
    options=list(OPERATING_MODE_KEYS.values()),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FroelingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the select entities that can be read and written."""
    data = entry.runtime_data
    sources = data.sources
    candidates = [
        (
            Component(ComponentKind.HEATING_CIRCUIT, n),
            HEATING_CIRCUIT_MODE,
            "operating_mode",
            HEATING_CIRCUIT_MODE_KEYS,
        )
        for n in data.equipment.heating_circuits
    ]
    candidates.append((BOILER_COMPONENT, SYSTEM_MODE, "system_state", OPERATING_MODE_KEYS))
    async_add_entities(
        FroelingSelect(sources, component, description, source, value_key=key, options=options)
        for component, description, suffix, options in candidates
        if (source := sources.source(key := component.key_prefix + suffix)) is not None
        and sources.writable(key)
    )


class FroelingSelect(FroelingWritableEntity, SelectEntity):
    """Choice between the codes of a value."""

    def __init__(
        self,
        sources: FroelingSources,
        component: Component,
        description: SelectEntityDescription,
        source: Source,
        *,
        value_key: str,
        options: dict[int, str],
    ) -> None:
        super().__init__(sources, component, description.key, source, value_key=value_key)
        self.entity_description = description
        self._options = options

    @property
    def current_option(self) -> str | None:
        """Current option; codes that cannot be selected show as unknown."""
        value = self.flat_value
        return self._options.get(value) if isinstance(value, int) else None

    async def async_select_option(self, option: str) -> None:
        """Write the code of the option."""
        await self.async_write(next(code for code, key in self._options.items() if key == option))
