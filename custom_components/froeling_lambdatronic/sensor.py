"""Sensors of the Fröling Lambdatronic integration."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from froeling_lambdatronic import LambdatronicValues, error_text
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
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import BOILER_STATE_KEYS, SYSTEM_STATE_KEYS
from .coordinator import FroelingConfigEntry, ParametersCoordinator, ValuesCoordinator
from .entity import BOILER_COMPONENT, Component, ComponentKind, FroelingEntity

PARALLEL_UPDATES = 0

NO_FAULT = "Keine Störung"


@dataclass(frozen=True, kw_only=True)
class FroelingSensorDescription(SensorEntityDescription):
    """Sensor reading one value of a component (number 0 for the boiler)."""

    value_fn: Callable[[LambdatronicValues, int], StateType]


def _temperature(
    key: str, value_fn: Callable[[LambdatronicValues, int], StateType], *, enabled: bool = True
) -> FroelingSensorDescription:
    return FroelingSensorDescription(
        key=key,
        translation_key=key,
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_registry_enabled_default=enabled,
        value_fn=value_fn,
    )


def _state_key(keys: dict[int, str], code: int | None) -> str | None:
    return None if code is None else keys.get(code)


BOILER_SENSORS: tuple[FroelingSensorDescription, ...] = (
    _temperature("boiler_temperature", lambda v, _: v.boiler_temperature),
    _temperature("boiler_setpoint_calculated", lambda v, _: v.boiler_setpoint_calculated),
    _temperature("flue_gas_temperature", lambda v, _: v.flue_gas_temperature),
    _temperature("return_temperature", lambda v, _: v.return_temperature, enabled=False),
    _temperature("outside_temperature", lambda v, _: v.outside_temperature),
    FroelingSensorDescription(
        key="residual_oxygen",
        translation_key="residual_oxygen",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda v, _: v.residual_oxygen,
    ),
    FroelingSensorDescription(
        key="induced_draft_speed",
        translation_key="induced_draft_speed",
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda v, _: v.induced_draft_speed,
    ),
    FroelingSensorDescription(
        key="feed_rate",
        translation_key="feed_rate",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda v, _: v.feed_rate,
    ),
    FroelingSensorDescription(
        key="boiler_output",
        translation_key="boiler_output",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda v, _: v.boiler_output,
    ),
    FroelingSensorDescription(
        key="pellet_hopper_level",
        translation_key="pellet_hopper_level",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda v, _: v.pellet_hopper_level,
    ),
    FroelingSensorDescription(
        key="pellet_consumption_total",
        translation_key="pellet_consumption_total",
        device_class=SensorDeviceClass.WEIGHT,
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=0,
        value_fn=lambda v, _: (
            None if v.pellet_consumption_total is None else round(v.pellet_consumption_total * 1000)
        ),
    ),
    FroelingSensorDescription(
        key="pellet_counter_kg",
        translation_key="pellet_counter_kg",
        device_class=SensorDeviceClass.WEIGHT,
        native_unit_of_measurement=UnitOfMass.KILOGRAMS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda v, _: v.pellet_counter_kg,
    ),
    FroelingSensorDescription(
        key="system_state",
        translation_key="system_state",
        device_class=SensorDeviceClass.ENUM,
        options=list(SYSTEM_STATE_KEYS.values()),
        value_fn=lambda v, _: _state_key(SYSTEM_STATE_KEYS, v.system_state),
    ),
    FroelingSensorDescription(
        key="boiler_state",
        translation_key="boiler_state",
        device_class=SensorDeviceClass.ENUM,
        options=list(BOILER_STATE_KEYS.values()),
        value_fn=lambda v, _: _state_key(BOILER_STATE_KEYS, v.boiler_state),
    ),
    FroelingSensorDescription(
        key="operating_hours",
        translation_key="operating_hours",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda v, _: v.operating_hours,
    ),
    FroelingSensorDescription(
        key="heating_hours",
        translation_key="heating_hours",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda v, _: v.heating_hours,
    ),
    FroelingSensorDescription(
        key="burner_starts",
        translation_key="burner_starts",
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda v, _: v.burner_starts,
    ),
    FroelingSensorDescription(
        key="hours_until_ash_emptying",
        translation_key="hours_until_ash_emptying",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda v, _: v.hours_until_ash_emptying,
    ),
)

HEATING_CIRCUIT_SENSORS: tuple[FroelingSensorDescription, ...] = (
    _temperature("flow_temperature", lambda v, n: v.heating_circuits[n].flow_temperature),
    _temperature("flow_setpoint", lambda v, n: v.heating_circuits[n].flow_setpoint),
    _temperature(
        "room_temperature",
        lambda v, n: v.heating_circuits[n].room_temperature,
        enabled=False,
    ),
)

HOT_WATER_SENSORS: tuple[FroelingSensorDescription, ...] = (
    _temperature("temperature", lambda v, n: v.hot_water[n].temperature),
    FroelingSensorDescription(
        key="pump",
        translation_key="pump",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda v, n: v.hot_water[n].pump,
    ),
)

BUFFER_SENSORS: tuple[FroelingSensorDescription, ...] = (
    _temperature("top", lambda v, n: v.buffers[n].top),
    _temperature("bottom", lambda v, n: v.buffers[n].bottom),
    FroelingSensorDescription(
        key="pump",
        translation_key="pump",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda v, n: v.buffers[n].pump,
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
    """Set up the sensors."""
    data = entry.runtime_data
    values = data.values
    entities: list[SensorEntity] = [
        FroelingSensor(values, BOILER_COMPONENT, description) for description in BOILER_SENSORS
    ]
    entities.append(FroelingFaultSensor(values, BOILER_COMPONENT, FAULT))
    for n in data.equipment.heating_circuits:
        component = Component(ComponentKind.HEATING_CIRCUIT, n)
        entities += [FroelingSensor(values, component, d) for d in HEATING_CIRCUIT_SENSORS]
        entities.append(FroelingCurveSensor(values, data.parameters, component))
    for n in data.equipment.hot_water:
        component = Component(ComponentKind.HOT_WATER, n)
        entities += [FroelingSensor(values, component, d) for d in HOT_WATER_SENSORS]
    for n in data.equipment.buffers:
        component = Component(ComponentKind.BUFFER, n)
        entities += [FroelingSensor(values, component, d) for d in BUFFER_SENSORS]
    async_add_entities(entities)


class FroelingSensor(FroelingEntity[ValuesCoordinator], SensorEntity):
    """Sensor for one value of a component."""

    entity_description: FroelingSensorDescription

    def __init__(
        self, coordinator: ValuesCoordinator, component: Component, description: FroelingSensorDescription
    ) -> None:
        super().__init__(coordinator, component, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType:
        """Current value."""
        return self.entity_description.value_fn(self.coordinator.data, self.component.number)


class FroelingFaultSensor(FroelingEntity[ValuesCoordinator], SensorEntity):
    """Text of the first entry in the error buffer; further entries as attribute."""

    def __init__(
        self, coordinator: ValuesCoordinator, component: Component, description: SensorEntityDescription
    ) -> None:
        super().__init__(coordinator, component, description.key)
        self.entity_description = description

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


class FroelingCurveSensor(FroelingEntity[ValuesCoordinator], SensorEntity):
    """Flow temperature of the heating curve at the current outside temperature."""

    entity_description = CURVE_FLOW
    # The curve only changes with the parameters; recording it with every state wastes space.
    _unrecorded_attributes = frozenset({"curve"})

    def __init__(
        self, coordinator: ValuesCoordinator, parameters: ParametersCoordinator, component: Component
    ) -> None:
        super().__init__(coordinator, component, CURVE_FLOW.key)
        self._parameters = parameters

    async def async_added_to_hass(self) -> None:
        """Also follow the parameters coordinator."""
        await super().async_added_to_hass()
        self.async_on_remove(self._parameters.async_add_listener(self._handle_parameters_update))

    @callback
    def _handle_parameters_update(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Needs both current values and parameters."""
        return super().available and self._parameters.last_update_success

    @property
    def native_value(self) -> float | None:
        """Flow temperature for the current outside temperature."""
        outside = self.coordinator.data.outside_temperature
        if outside is None:
            return None
        return self._parameters.data.heating_circuits[self.component.number].flow_for_outside(outside)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Points of the heating curve from -20 to +20 °C."""
        curve = self._parameters.data.heating_circuits[self.component.number].curve()
        return {"curve": [{"outside": outside, "flow": flow} for outside, flow in curve]}
