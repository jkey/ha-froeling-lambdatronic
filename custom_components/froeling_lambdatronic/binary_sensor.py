"""Binary sensors of the Fröling Lambdatronic integration."""

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FroelingConfigEntry
from .entity import BOILER_COMPONENT, Component, FroelingEntity
from .sources import FroelingSources, Source

PARALLEL_UPDATES = 0

FAULT_ACTIVE = BinarySensorEntityDescription(
    key="fault_active",
    translation_key="fault_active",
    device_class=BinarySensorDeviceClass.PROBLEM,
)

BOILER_BINARY_SENSORS: tuple[BinarySensorEntityDescription, ...] = (
    BinarySensorEntityDescription(key="boiler_demand", translation_key="boiler_demand"),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FroelingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors of every value that has a source."""
    data = entry.runtime_data
    sources = data.sources
    entities: list[BinarySensorEntity] = _binary_sensors(sources, BOILER_COMPONENT, BOILER_BINARY_SENSORS)
    if data.values is not None:
        entities.append(FroelingFaultBinarySensor(sources))
    async_add_entities(entities)


def _binary_sensors(
    sources: FroelingSources, component: Component, descriptions: tuple[BinarySensorEntityDescription, ...]
) -> list[BinarySensorEntity]:
    return [
        FroelingBinarySensor(sources, component, description, source)
        for description in descriptions
        if (source := sources.source(component.key_prefix + description.key)) is not None
    ]


class FroelingBinarySensor(FroelingEntity, BinarySensorEntity):
    """Binary sensor of one on/off value."""

    def __init__(
        self,
        sources: FroelingSources,
        component: Component,
        description: BinarySensorEntityDescription,
        source: Source,
    ) -> None:
        super().__init__(sources, component, description.key, source)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Current state."""
        value = self.flat_value
        return None if value is None else bool(value)


class FroelingFaultBinarySensor(FroelingEntity, BinarySensorEntity):
    """On while the Modbus error buffer has entries or the boiler is in a fault state."""

    entity_description = FAULT_ACTIVE

    def __init__(self, sources: FroelingSources) -> None:
        super().__init__(sources, BOILER_COMPONENT, FAULT_ACTIVE.key, Source.MODBUS_VALUES)

    @property
    def is_on(self) -> bool:
        """True if a fault is active."""
        return bool(self.coordinator.data.fault_active)
