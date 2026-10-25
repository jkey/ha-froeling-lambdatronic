"""Binary sensors of the Fröling Lambdatronic integration."""

from collections.abc import Callable
from dataclasses import dataclass

from froeling_lambdatronic import LambdatronicValues
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FroelingConfigEntry, ValuesCoordinator
from .entity import BOILER_COMPONENT, Component, FroelingEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class FroelingBinarySensorDescription(BinarySensorEntityDescription):
    """Binary sensor derived from the current values."""

    value_fn: Callable[[LambdatronicValues], bool | None]


BINARY_SENSORS: tuple[FroelingBinarySensorDescription, ...] = (
    FroelingBinarySensorDescription(
        key="fault_active",
        translation_key="fault_active",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda v: v.fault_active,
    ),
    FroelingBinarySensorDescription(
        key="boiler_demand",
        translation_key="boiler_demand",
        value_fn=lambda v: v.boiler_demand,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FroelingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the binary sensors."""
    values = entry.runtime_data.values
    async_add_entities(
        FroelingBinarySensor(values, BOILER_COMPONENT, description) for description in BINARY_SENSORS
    )


class FroelingBinarySensor(FroelingEntity[ValuesCoordinator], BinarySensorEntity):
    """Binary sensor of the boiler."""

    entity_description: FroelingBinarySensorDescription

    def __init__(
        self,
        coordinator: ValuesCoordinator,
        component: Component,
        description: FroelingBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, component, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Current state."""
        return self.entity_description.value_fn(self.coordinator.data)
