"""Number entities (writable settings) of the Fröling Lambdatronic integration."""

from froeling_lambdatronic import register_for_key
from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FroelingConfigEntry
from .entity import (
    BOILER_COMPONENT,
    CIRCULATION_COMPONENT,
    Component,
    ComponentKind,
    FroelingWritableEntity,
)
from .sources import FroelingSources, Source

PARALLEL_UPDATES = 1

UNIT_TONNES = "t"
UNIT_KELVIN_DIFFERENCE = "K"


def _setting(key: str, unit: str = UnitOfTemperature.CELSIUS) -> NumberEntityDescription:
    return NumberEntityDescription(
        key=key,
        translation_key=key,
        device_class=NumberDeviceClass.TEMPERATURE if unit == UnitOfTemperature.CELSIUS else None,
        native_unit_of_measurement=unit,
        entity_category=EntityCategory.CONFIG,
        mode=NumberMode.BOX,
    )


BOILER_NUMBERS: tuple[NumberEntityDescription, ...] = (
    _setting("boiler_setpoint"),
    _setting("pellet_stock", UNIT_TONNES),
    _setting("pellet_stock_minimum", UNIT_TONNES),
)

HEATING_CIRCUIT_NUMBERS: tuple[NumberEntityDescription, ...] = (
    _setting("curve_flow_plus10"),
    _setting("curve_flow_minus10"),
    _setting("setback", UNIT_KELVIN_DIFFERENCE),
    _setting("heating_limit"),
    _setting("heating_limit_setback"),
    _setting("frost_protection"),
    _setting("pump_off_below_flow_setpoint"),
    _setting("buffer_overheat_protection"),
)

# The setpoint is part of the water heater entity.
HOT_WATER_NUMBERS: tuple[NumberEntityDescription, ...] = (_setting("recharge_below"),)

BUFFER_NUMBERS: tuple[NumberEntityDescription, ...] = (_setting("circuit_release"),)

CIRCULATION_NUMBERS: tuple[NumberEntityDescription, ...] = (_setting("pump_off_above"),)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FroelingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a number entity for every setting that can be read and written."""
    data = entry.runtime_data
    sources = data.sources
    entities = _numbers(sources, BOILER_COMPONENT, BOILER_NUMBERS)
    for n in data.equipment.heating_circuits:
        entities += _numbers(sources, Component(ComponentKind.HEATING_CIRCUIT, n), HEATING_CIRCUIT_NUMBERS)
    for n in data.equipment.hot_water:
        entities += _numbers(sources, Component(ComponentKind.HOT_WATER, n), HOT_WATER_NUMBERS)
    for n in data.equipment.buffers:
        entities += _numbers(sources, Component(ComponentKind.BUFFER, n), BUFFER_NUMBERS)
    if data.equipment.circulation_pump:
        entities += _numbers(sources, CIRCULATION_COMPONENT, CIRCULATION_NUMBERS)
    async_add_entities(entities)


def _numbers(
    sources: FroelingSources, component: Component, descriptions: tuple[NumberEntityDescription, ...]
) -> list[FroelingNumber]:
    return [
        FroelingNumber(sources, component, description, source)
        for description in descriptions
        if (source := sources.source(key := component.key_prefix + description.key)) is not None
        and sources.writable(key)
    ]


class FroelingNumber(FroelingWritableEntity, NumberEntity):
    """Writable setting; limits from the write path, step from the register catalogue of the library."""

    def __init__(
        self,
        sources: FroelingSources,
        component: Component,
        description: NumberEntityDescription,
        source: Source,
    ) -> None:
        super().__init__(sources, component, description.key, source)
        self.entity_description = description
        # Limits of the write path: the register via Modbus, the cloud parameter via Fröling Connect
        # (which checks them again on writing). Every setting has a register; its step is used for both.
        register = register_for_key(self.value_key)
        assert register is not None
        minimum, maximum = register.minimum, register.maximum
        if self.write_source is Source.CLOUD and (limits := sources.cloud_limits(self.value_key)) is not None:
            minimum, maximum = limits
        if minimum is not None:
            self._attr_native_min_value = minimum
        if maximum is not None:
            self._attr_native_max_value = maximum
        if register.step is not None:
            self._attr_native_step = register.step

    @property
    def native_value(self) -> float | None:
        """Current setting."""
        value = self.flat_value
        return float(value) if isinstance(value, float | int) else None

    async def async_set_native_value(self, value: float) -> None:
        """Write the setting."""
        await self.async_write(value)
