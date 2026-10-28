"""Binary sensors of the Fröling Lambdatronic integration."""

from froeling_lambdatronic import FAULT_BOILER_STATES
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FroelingConfigEntry
from .entity import BOILER_COMPONENT, CIRCULATION_COMPONENT, Component, ComponentKind, FroelingEntity
from .sources import FroelingSources, Source

PARALLEL_UPDATES = 0

FAULT_ACTIVE = BinarySensorEntityDescription(
    key="fault_active",
    translation_key="fault_active",
    device_class=BinarySensorDeviceClass.PROBLEM,
)

CONNECT = BinarySensorEntityDescription(
    key="connect",
    translation_key="connect",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    entity_category=EntityCategory.DIAGNOSTIC,
)

BOILER_BINARY_SENSORS: tuple[BinarySensorEntityDescription, ...] = (
    BinarySensorEntityDescription(key="boiler_demand", translation_key="boiler_demand"),
)

HEATING_CIRCUIT_BINARY_SENSORS: tuple[BinarySensorEntityDescription, ...] = (
    BinarySensorEntityDescription(
        key="pump", translation_key="pump", device_class=BinarySensorDeviceClass.RUNNING
    ),
)

CIRCULATION_BINARY_SENSORS: tuple[BinarySensorEntityDescription, ...] = (
    BinarySensorEntityDescription(key="flow_switch", translation_key="flow_switch"),
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
    entities.append(FroelingFaultBinarySensor(sources, data.values is not None))
    if data.cloud_info is not None:
        entities.append(FroelingConnectBinarySensor(sources))
    for n in data.equipment.heating_circuits:
        component = Component(ComponentKind.HEATING_CIRCUIT, n)
        entities += _binary_sensors(sources, component, HEATING_CIRCUIT_BINARY_SENSORS)
    if data.equipment.circulation_pump:
        entities += _binary_sensors(sources, CIRCULATION_COMPONENT, CIRCULATION_BINARY_SENSORS)
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
    """On while a fault is active.

    With Modbus: the error buffer has entries or the boiler is in a fault state. Connect only:
    the boiler state reported by Fröling Connect is a fault state (its error list is a history
    without an 'active' flag).
    """

    entity_description = FAULT_ACTIVE

    def __init__(self, sources: FroelingSources, modbus: bool) -> None:
        source = Source.MODBUS_VALUES if modbus else Source.CLOUD
        super().__init__(sources, BOILER_COMPONENT, FAULT_ACTIVE.key, source, value_key="boiler_state")
        self._modbus = modbus

    @property
    def is_on(self) -> bool | None:
        """True if a fault is active."""
        if self._modbus:
            return bool(self.coordinator.data.fault_active)
        state = self.flat_value
        return None if not isinstance(state, int) else state in FAULT_BOILER_STATES


class FroelingConnectBinarySensor(FroelingEntity, BinarySensorEntity):
    """On while Fröling Connect reports the boiler as online."""

    entity_description = CONNECT

    def __init__(self, sources: FroelingSources) -> None:
        super().__init__(sources, BOILER_COMPONENT, CONNECT.key, Source.CLOUD_INFO)

    @property
    def is_on(self) -> bool:
        """True if the boiler is connected to Fröling Connect."""
        return bool(self.coordinator.data.header.online)
