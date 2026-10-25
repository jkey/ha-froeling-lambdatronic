"""Number entities (writable settings) of the Fröling Lambdatronic integration."""

from collections.abc import Callable
from dataclasses import dataclass

from froeling_lambdatronic import (
    BOILER,
    LambdatronicParameters,
    RegisterDef,
    heating_circuit,
    hot_water,
)
from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FroelingConfigEntry, ParametersCoordinator
from .entity import BOILER_COMPONENT, Component, ComponentKind, FroelingEntity, async_write_register

PARALLEL_UPDATES = 1

UNIT_TONNES = "t"
UNIT_KELVIN_DIFFERENCE = "K"


@dataclass(frozen=True, kw_only=True)
class FroelingNumberDescription(NumberEntityDescription):
    """Writable setting of a component (number 0 for the boiler)."""

    register_fn: Callable[[int], RegisterDef]
    value_fn: Callable[[LambdatronicParameters, int], float | None]


def _temperature(
    key: str,
    register_fn: Callable[[int], RegisterDef],
    value_fn: Callable[[LambdatronicParameters, int], float | None],
) -> FroelingNumberDescription:
    return FroelingNumberDescription(
        key=key,
        translation_key=key,
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
        register_fn=register_fn,
        value_fn=value_fn,
    )


BOILER_NUMBERS: tuple[FroelingNumberDescription, ...] = (
    _temperature("boiler_setpoint", lambda _: BOILER.boiler_setpoint, lambda p, _: p.boiler_setpoint),
    FroelingNumberDescription(
        key="pellet_stock",
        translation_key="pellet_stock",
        native_unit_of_measurement=UNIT_TONNES,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
        register_fn=lambda _: BOILER.pellet_stock,
        value_fn=lambda p, _: p.pellet_stock,
    ),
    FroelingNumberDescription(
        key="pellet_stock_minimum",
        translation_key="pellet_stock_minimum",
        native_unit_of_measurement=UNIT_TONNES,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
        register_fn=lambda _: BOILER.pellet_stock_minimum,
        value_fn=lambda p, _: p.pellet_stock_minimum,
    ),
)

HEATING_CIRCUIT_NUMBERS: tuple[FroelingNumberDescription, ...] = (
    _temperature(
        "curve_flow_plus10",
        lambda n: heating_circuit(n).curve_flow_plus10,
        lambda p, n: p.heating_circuits[n].curve_flow_plus10,
    ),
    _temperature(
        "curve_flow_minus10",
        lambda n: heating_circuit(n).curve_flow_minus10,
        lambda p, n: p.heating_circuits[n].curve_flow_minus10,
    ),
    FroelingNumberDescription(
        key="setback",
        translation_key="setback",
        native_unit_of_measurement=UNIT_KELVIN_DIFFERENCE,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
        register_fn=lambda n: heating_circuit(n).setback,
        value_fn=lambda p, n: p.heating_circuits[n].setback,
    ),
    _temperature(
        "heating_limit",
        lambda n: heating_circuit(n).heating_limit,
        lambda p, n: p.heating_circuits[n].heating_limit,
    ),
    _temperature(
        "heating_limit_setback",
        lambda n: heating_circuit(n).heating_limit_setback,
        lambda p, n: p.heating_circuits[n].heating_limit_setback,
    ),
)

HOT_WATER_NUMBERS: tuple[FroelingNumberDescription, ...] = (
    _temperature("setpoint", lambda n: hot_water(n).setpoint, lambda p, n: p.hot_water[n].setpoint),
    _temperature(
        "recharge_below", lambda n: hot_water(n).recharge_below, lambda p, n: p.hot_water[n].recharge_below
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FroelingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the number entities."""
    data = entry.runtime_data
    parameters = data.parameters
    entities = [FroelingNumber(parameters, BOILER_COMPONENT, d) for d in BOILER_NUMBERS]
    for n in data.equipment.heating_circuits:
        component = Component(ComponentKind.HEATING_CIRCUIT, n)
        entities += [FroelingNumber(parameters, component, d) for d in HEATING_CIRCUIT_NUMBERS]
    for n in data.equipment.hot_water:
        component = Component(ComponentKind.HOT_WATER, n)
        entities += [FroelingNumber(parameters, component, d) for d in HOT_WATER_NUMBERS]
    async_add_entities(entities)


class FroelingNumber(FroelingEntity[ParametersCoordinator], NumberEntity):
    """Writable setting; limits and step come from the register catalogue of the library."""

    entity_description: FroelingNumberDescription

    def __init__(
        self,
        coordinator: ParametersCoordinator,
        component: Component,
        description: FroelingNumberDescription,
    ) -> None:
        super().__init__(coordinator, component, description.key)
        self.entity_description = description
        self._register = description.register_fn(component.number)
        if self._register.minimum is not None:
            self._attr_native_min_value = self._register.minimum
        if self._register.maximum is not None:
            self._attr_native_max_value = self._register.maximum
        if self._register.step is not None:
            self._attr_native_step = self._register.step

    @property
    def native_value(self) -> float | None:
        """Current setting."""
        return self.entity_description.value_fn(self.coordinator.data, self.component.number)

    async def async_set_native_value(self, value: float) -> None:
        """Write the setting to the boiler."""
        await async_write_register(self.coordinator, self._register, value)
