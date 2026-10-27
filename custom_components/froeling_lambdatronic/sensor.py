"""Sensors of the Fröling Lambdatronic integration."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import time
from typing import Any

from froeling_lambdatronic import FlatValue, HeatingCircuitParameters, error_text
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    REVOLUTIONS_PER_MINUTE,
    EntityCategory,
    UnitOfMass,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import BOILER_STATE_KEYS, SYSTEM_STATE_KEYS
from .coordinator import FroelingConfigEntry
from .entity import BOILER_COMPONENT, Component, ComponentKind, FroelingEntity
from .sources import FroelingSources, Source

PARALLEL_UPDATES = 0

NO_FAULT = "Keine Störung"


def _state(value: FlatValue) -> StateType:
    return None if isinstance(value, time) else value


@dataclass(frozen=True, kw_only=True)
class FroelingSensorDescription(SensorEntityDescription):
    """Sensor reading one value of a component.

    `value_key` is the library key without the component prefix (defaults to `key`);
    `convert` turns the library value into the state.
    """

    value_key: str | None = None
    convert: Callable[[FlatValue], StateType] = _state


def _temperature(key: str, *, enabled: bool = True) -> FroelingSensorDescription:
    return FroelingSensorDescription(
        key=key,
        translation_key=key,
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=enabled,
    )


def _state_key(keys: dict[int, str]) -> Callable[[FlatValue], str | None]:
    def convert(code: FlatValue) -> str | None:
        return keys.get(code) if isinstance(code, int) else None

    return convert


def _tonnes_to_kg(value: FlatValue) -> int | None:
    return None if not isinstance(value, float | int) else round(value * 1000)


BOILER_SENSORS: tuple[FroelingSensorDescription, ...] = (
    _temperature("boiler_temperature"),
    _temperature("boiler_setpoint_calculated"),
    _temperature("flue_gas_temperature"),
    _temperature("return_temperature", enabled=False),
    _temperature("outside_temperature"),
    FroelingSensorDescription(
        key="residual_oxygen",
        translation_key="residual_oxygen",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    FroelingSensorDescription(
        key="induced_draft_speed",
        translation_key="induced_draft_speed",
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    FroelingSensorDescription(
        key="feed_rate",
        translation_key="feed_rate",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    FroelingSensorDescription(
        key="boiler_control_output",
        translation_key="boiler_control_output",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    FroelingSensorDescription(
        key="pellet_hopper_level",
        translation_key="pellet_hopper_level",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
    ),
    FroelingSensorDescription(
        key="pellet_consumption_total",
        translation_key="pellet_consumption_total",
        device_class=SensorDeviceClass.WEIGHT,
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        convert=_tonnes_to_kg,
    ),
    FroelingSensorDescription(
        key="pellet_counter",
        translation_key="pellet_counter",
        device_class=SensorDeviceClass.WEIGHT,
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    FroelingSensorDescription(
        key="system_state",
        translation_key="system_state",
        device_class=SensorDeviceClass.ENUM,
        options=list(SYSTEM_STATE_KEYS.values()),
        convert=_state_key(SYSTEM_STATE_KEYS),
    ),
    FroelingSensorDescription(
        key="boiler_state",
        translation_key="boiler_state",
        device_class=SensorDeviceClass.ENUM,
        options=list(BOILER_STATE_KEYS.values()),
        convert=_state_key(BOILER_STATE_KEYS),
    ),
    FroelingSensorDescription(
        key="operating_hours",
        translation_key="operating_hours",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FroelingSensorDescription(
        key="heating_hours",
        translation_key="heating_hours",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FroelingSensorDescription(
        key="burner_starts",
        translation_key="burner_starts",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FroelingSensorDescription(
        key="hours_until_ash_emptying",
        translation_key="hours_until_ash_emptying",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)

HEATING_CIRCUIT_SENSORS: tuple[FroelingSensorDescription, ...] = (
    _temperature("flow_temperature"),
    _temperature("flow_setpoint"),
    _temperature("room_temperature", enabled=False),
)

HOT_WATER_SENSORS: tuple[FroelingSensorDescription, ...] = (
    _temperature("temperature"),
    FroelingSensorDescription(
        key="pump",
        translation_key="pump",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
)

BUFFER_SENSORS: tuple[FroelingSensorDescription, ...] = (
    _temperature("top"),
    _temperature("bottom"),
    FroelingSensorDescription(
        key="pump",
        translation_key="pump",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
)

CURVE_FLOW = SensorEntityDescription(
    key="curve_flow",
    translation_key="curve_flow",
    device_class=SensorDeviceClass.TEMPERATURE,
    native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    state_class=SensorStateClass.MEASUREMENT,
)

FAULT = SensorEntityDescription(key="fault", translation_key="fault")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FroelingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors of every value that has a source."""
    data = entry.runtime_data
    sources = data.sources
    entities: list[SensorEntity] = _sensors(sources, BOILER_COMPONENT, BOILER_SENSORS)
    if data.values is not None:
        entities.append(FroelingFaultSensor(sources, BOILER_COMPONENT))
    for n in data.equipment.heating_circuits:
        component = Component(ComponentKind.HEATING_CIRCUIT, n)
        entities += _sensors(sources, component, HEATING_CIRCUIT_SENSORS)
        if (curve := FroelingCurveSensor.create(sources, component)) is not None:
            entities.append(curve)
    for n in data.equipment.hot_water:
        entities += _sensors(sources, Component(ComponentKind.HOT_WATER, n), HOT_WATER_SENSORS)
    for n in data.equipment.buffers:
        entities += _sensors(sources, Component(ComponentKind.BUFFER, n), BUFFER_SENSORS)
    async_add_entities(entities)


def _sensors(
    sources: FroelingSources, component: Component, descriptions: tuple[FroelingSensorDescription, ...]
) -> list[SensorEntity]:
    return [
        FroelingSensor(sources, component, description, source)
        for description in descriptions
        if (source := sources.source(_value_key(component, description))) is not None
    ]


def _value_key(component: Component, description: FroelingSensorDescription) -> str:
    return component.key_prefix + (description.value_key or description.key)


class FroelingSensor(FroelingEntity, SensorEntity):
    """Sensor for one value of a component."""

    entity_description: FroelingSensorDescription

    def __init__(
        self,
        sources: FroelingSources,
        component: Component,
        description: FroelingSensorDescription,
        source: Source,
    ) -> None:
        super().__init__(
            sources, component, description.key, source, value_key=_value_key(component, description)
        )
        self.entity_description = description

    @property
    def native_value(self) -> StateType:
        """Current value."""
        return self.entity_description.convert(self.flat_value)


class FroelingFaultSensor(FroelingEntity, SensorEntity):
    """Text of the first entry in the Modbus error buffer; further entries as attribute."""

    entity_description = FAULT

    def __init__(self, sources: FroelingSources, component: Component) -> None:
        super().__init__(sources, component, FAULT.key, Source.MODBUS_VALUES)

    @property
    def native_value(self) -> str:
        """Text of the first error or 'Keine Störung'."""
        errors = self.coordinator.data.errors
        return error_text(errors[0]) if errors else NO_FAULT

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Error code of the first entry and texts of further entries."""
        errors = self.coordinator.data.errors
        return {
            "error_code": errors[0] if errors else None,
            "more_errors": [error_text(code) for code in errors[1:]],
        }


class FroelingCurveSensor(FroelingEntity, SensorEntity):
    """Flow temperature of the heating curve at the current outside temperature."""

    entity_description = CURVE_FLOW
    # The curve only changes with the parameters; recording it with every state wastes space.
    _unrecorded_attributes = frozenset({"curve"})

    @classmethod
    def create(cls, sources: FroelingSources, component: Component) -> FroelingCurveSensor | None:
        """The sensor if the outside temperature and both curve points have a source."""
        keys = [f"{component.key_prefix}curve_flow_plus10", f"{component.key_prefix}curve_flow_minus10"]
        if (outside := sources.source("outside_temperature")) is None or any(
            sources.source(key) is None for key in keys
        ):
            return None
        return cls(sources, component, outside, keys)

    def __init__(
        self, sources: FroelingSources, component: Component, outside: Source, curve_keys: list[str]
    ) -> None:
        # The outside temperature is a system value without a component prefix.
        super().__init__(sources, component, CURVE_FLOW.key, outside, value_key="outside_temperature")
        for key in curve_keys:
            source = sources.source(key)
            assert source is not None
            self.follow(sources.coordinator(source))

    def _parameters(self) -> HeatingCircuitParameters:
        """The curve settings of this circuit; the library computes the curve from them."""
        prefix = self.component.key_prefix

        def number(key: str) -> float | None:
            value = self.sources.value(prefix + key)
            return float(value) if isinstance(value, float | int) else None

        return HeatingCircuitParameters(
            curve_flow_plus10=number("curve_flow_plus10"),
            curve_flow_minus10=number("curve_flow_minus10"),
            setback=None,
            max_flow=number("max_flow"),
            heating_limit=None,
            heating_limit_setback=None,
            operating_mode=None,
            room_setpoint_heating=None,
            room_setpoint_setback=None,
            frost_protection=None,
            pump_off_below_flow_setpoint=None,
            buffer_overheat_protection=None,
        )

    @property
    def native_value(self) -> float | None:
        """Flow temperature for the current outside temperature."""
        outside = self.flat_value
        if not isinstance(outside, float | int):
            return None
        return self._parameters().flow_for_outside(outside)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Points of the heating curve from -20 to +20 °C."""
        return {"curve": [{"outside": outside, "flow": flow} for outside, flow in self._parameters().curve()]}
