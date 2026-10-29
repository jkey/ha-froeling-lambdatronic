"""Sensors of the Fröling Lambdatronic integration."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, time
from typing import Any

from froeling_lambdatronic import (
    FAULT_BOILER_STATES,
    CloudError,
    FlatValue,
    HeatingCircuitParameters,
    boiler_state_text,
    error_text,
)
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

from .const import BOILER_STATE_KEYS
from .coordinator import CloudInfo, FroelingConfigEntry
from .entity import BOILER_COMPONENT, CIRCULATION_COMPONENT, Component, ComponentKind, FroelingEntity
from .sources import FroelingSources, Source

PARALLEL_UPDATES = 0

NO_FAULT = "Keine Störung"
HISTORY_LENGTH = 10


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
    # Only created if the value is known during setup (e.g. an optional probe).
    requires_value: bool = False
    # False: disabled by default when read from Fröling Connect.
    enabled_from_cloud: bool = True


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
    # No system state sensor: the system operating mode select shows it.
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
    FroelingSensorDescription(
        key="hours_since_maintenance",
        translation_key="hours_since_maintenance",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        # Reset by the service technician; HA treats the drop as a new cycle.
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    FroelingSensorDescription(
        key="induced_draft_control",
        translation_key="induced_draft_control",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
)

HEATING_CIRCUIT_SENSORS: tuple[FroelingSensorDescription, ...] = (
    _temperature("flow_temperature"),
    _temperature("flow_setpoint"),
    _temperature("room_temperature", enabled=False),
)

# No tank temperature sensor: the water heater shows it as its current temperature.
HOT_WATER_SENSORS: tuple[FroelingSensorDescription, ...] = (
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
    FroelingSensorDescription(
        key="charge",
        translation_key="charge",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    # Boiler setpoint minus the boiler start difference of this buffer.
    FroelingSensorDescription(
        key="ignition_below_buffer_temperature",
        translation_key="ignition_below_buffer_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
)

CIRCULATION_SENSORS: tuple[FroelingSensorDescription, ...] = (
    FroelingSensorDescription(
        key="pump",
        translation_key="pump",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    # Modbus reports no return temperature without a configured sensor (40603 = 0); Fröling
    # Connect reports 0 then, so the cloud value starts disabled.
    FroelingSensorDescription(
        key="return_temperature",
        translation_key="return_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        requires_value=True,
        enabled_from_cloud=False,
    ),
)


@dataclass(frozen=True, kw_only=True)
class FroelingCloudInfoSensorDescription(SensorEntityDescription):
    """Sensor showing system information or the connection state from Fröling Connect."""

    value_fn: Callable[[CloudInfo], StateType | datetime]


CLOUD_INFO_SENSORS: tuple[FroelingCloudInfoSensorDescription, ...] = (
    FroelingCloudInfoSensorDescription(
        key="partial_load_hours",
        translation_key="partial_load_hours",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda info: info.system.partial_load_hours,
    ),
    FroelingCloudInfoSensorDescription(
        key="connected_since",
        translation_key="connected_since",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda info: info.system.connected_since,
    ),
    FroelingCloudInfoSensorDescription(
        key="last_signal",
        translation_key="last_signal",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda info: info.header.last_signal,
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
    entities.append(FroelingFaultSensor(sources, data.values is not None, data.cloud is not None))
    if data.cloud_info is not None:
        entities += [FroelingCloudInfoSensor(sources, description) for description in CLOUD_INFO_SENSORS]
    for n in data.equipment.heating_circuits:
        component = Component(ComponentKind.HEATING_CIRCUIT, n)
        entities += _sensors(sources, component, HEATING_CIRCUIT_SENSORS)
        if (curve := FroelingCurveSensor.create(sources, component)) is not None:
            entities.append(curve)
    for n in data.equipment.hot_water:
        entities += _sensors(sources, Component(ComponentKind.HOT_WATER, n), HOT_WATER_SENSORS)
    for n in data.equipment.buffers:
        entities += _sensors(sources, Component(ComponentKind.BUFFER, n), BUFFER_SENSORS)
    if data.equipment.circulation_pump:
        entities += _sensors(sources, CIRCULATION_COMPONENT, CIRCULATION_SENSORS)
    async_add_entities(entities)


def _sensors(
    sources: FroelingSources, component: Component, descriptions: tuple[FroelingSensorDescription, ...]
) -> list[SensorEntity]:
    return [
        FroelingSensor(sources, component, description, source)
        for description in descriptions
        if (source := sources.source(key := _value_key(component, description))) is not None
        and (not description.requires_value or sources.value(key) is not None)
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
        if source is Source.CLOUD and not description.enabled_from_cloud:
            self._attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> StateType:
        """Current value."""
        return self.entity_description.convert(self.flat_value)


class FroelingCloudInfoSensor(FroelingEntity, SensorEntity):
    """System information or connection state of the boiler from Fröling Connect."""

    entity_description: FroelingCloudInfoSensorDescription

    def __init__(self, sources: FroelingSources, description: FroelingCloudInfoSensorDescription) -> None:
        super().__init__(sources, BOILER_COMPONENT, description.key, Source.CLOUD_INFO)
        self.entity_description = description

    @property
    def native_value(self) -> StateType | datetime:
        """Current value."""
        return self.entity_description.value_fn(self.coordinator.data)


class FroelingFaultSensor(FroelingEntity, SensorEntity):
    """Current fault as text.

    With Modbus: the first entry of the error buffer, further entries as attribute. Connect only:
    the newest message of the error history while the boiler is in a fault state. With Connect the
    time of the newest message and the last ten messages are attributes as well.
    """

    entity_description = FAULT
    # The history only grows with new messages; recording it with every state wastes space.
    _unrecorded_attributes = frozenset({"since", "history"})

    def __init__(self, sources: FroelingSources, modbus: bool, cloud: bool) -> None:
        source = Source.MODBUS_VALUES if modbus else Source.CLOUD
        super().__init__(sources, BOILER_COMPONENT, FAULT.key, source, value_key="boiler_state")
        self._modbus = modbus
        self._cloud = sources.coordinator(Source.CLOUD) if cloud else None
        if self._cloud is not None:
            # Next to Modbus the history is an addition: the sensor stays available without it.
            self.follow(self._cloud, required=False)

    @property
    def native_value(self) -> str:
        """Text of the current fault or 'Keine Störung'."""
        if self._modbus:
            errors = self.coordinator.data.errors
            return error_text(errors[0]) if errors else NO_FAULT
        state = self.sources.value("boiler_state")
        if not isinstance(state, int) or state not in FAULT_BOILER_STATES:
            return NO_FAULT
        history = _history(self._cloud_errors)
        return history[0].message if history else boiler_state_text(state)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Error code and further errors (Modbus); time of the fault and history (Connect)."""
        attributes: dict[str, Any] = {}
        if self._modbus:
            errors = self.coordinator.data.errors
            attributes["error_code"] = errors[0] if errors else None
            attributes["more_errors"] = [error_text(code) for code in errors[1:]]
        if self._cloud is not None:
            history = _history(self._cloud_errors)
            since = history[0].timestamp if history and self._fault_active else None
            attributes["since"] = None if since is None else since.isoformat()
            attributes["history"] = [
                {
                    "time": None if error.timestamp is None else error.timestamp.isoformat(),
                    "severity": error.severity,
                    "message": error.message,
                }
                for error in history
            ]
        return attributes

    @property
    def _fault_active(self) -> bool:
        if self._modbus:
            return bool(self.coordinator.data.fault_active)
        return self.native_value != NO_FAULT

    @property
    def _cloud_errors(self) -> tuple[CloudError, ...]:
        cloud = self._cloud
        # No stale history while the cloud fails beyond its tolerance.
        if cloud is None or cloud.data is None or not cloud.last_update_success:
            return ()
        errors: tuple[CloudError, ...] = cloud.data.errors
        return errors


def _history(errors: tuple[CloudError, ...]) -> list[CloudError]:
    """The newest ten messages, newest first; messages without a time last."""
    return sorted(
        errors,
        key=lambda e: (e.timestamp is not None, 0.0 if e.timestamp is None else e.timestamp.timestamp()),
        reverse=True,
    )[:HISTORY_LENGTH]


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
        # The maximum flow limits the curve; without it the curve is not limited.
        if (max_flow := sources.source(f"{component.key_prefix}max_flow")) is not None:
            self.follow(sources.coordinator(max_flow), required=False)

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
