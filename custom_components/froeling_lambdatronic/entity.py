"""Base entity and devices of the Fröling Lambdatronic integration."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from froeling_lambdatronic import Equipment, FlatValue
from homeassistant.core import callback
from homeassistant.helpers.device_registry import ChildDeviceInfo, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .const import DOMAIN
from .coordinator import FroelingConfigEntry
from .sources import FroelingSources, Source, WriteValue


class ComponentKind(StrEnum):
    """Kind of a device; the value is the key prefix used by the library (e.g. 'hc' in 'hc1_setback')."""

    BOILER = "boiler"
    HEATING_CIRCUIT = "hc"
    HOT_WATER = "dhw"
    BUFFER = "buffer"
    CIRCULATION = "circulation"


DEVICE_TRANSLATION_KEYS = {
    ComponentKind.HEATING_CIRCUIT: "heating_circuit",
    ComponentKind.HOT_WATER: "hot_water",
    ComponentKind.BUFFER: "buffer",
    ComponentKind.CIRCULATION: "circulation_pump",
}


@dataclass(frozen=True, slots=True)
class Component:
    """The boiler itself, the circulation pump (number 0) or a numbered heating circuit, tank or buffer."""

    kind: ComponentKind
    number: int = 0

    @property
    def key(self) -> str:
        """Component key as used by the library: 'boiler', 'hc1', 'dhw1', 'buffer1', 'circulation'."""
        if self.kind in (ComponentKind.BOILER, ComponentKind.CIRCULATION):
            return str(self.kind)
        return f"{self.kind}{self.number}"

    @property
    def key_prefix(self) -> str:
        """Prefix of value keys: '' for the boiler, e.g. 'hc1_' or 'circulation_' otherwise."""
        return "" if self.kind is ComponentKind.BOILER else f"{self.key}_"

    def identifier(self, entry_id: str) -> str:
        """Device registry identifier."""
        return entry_id if self.kind is ComponentKind.BOILER else f"{entry_id}_{self.key}"


BOILER_COMPONENT = Component(ComponentKind.BOILER)
CIRCULATION_COMPONENT = Component(ComponentKind.CIRCULATION)


def components(equipment: Equipment) -> list[Component]:
    """All devices for the given equipment, the boiler first."""
    return [
        BOILER_COMPONENT,
        *(Component(ComponentKind.HEATING_CIRCUIT, n) for n in equipment.heating_circuits),
        *(Component(ComponentKind.HOT_WATER, n) for n in equipment.hot_water),
        *(Component(ComponentKind.BUFFER, n) for n in equipment.buffers),
        *([CIRCULATION_COMPONENT] if equipment.circulation_pump else []),
    ]


def connect_name(entry: FroelingConfigEntry, component: Component) -> str | None:
    """Name the user gave the component in Fröling Connect; None without Connect data."""
    cloud = entry.runtime_data.cloud
    if cloud is None or cloud.data is None:
        return None
    for item in cloud.data.snapshot.components:
        if item.key == component.key:
            return item.display_name or item.standard_name or None
    return None


def child_device_info(entry: FroelingConfigEntry, component: Component) -> ChildDeviceInfo:
    """Child device of the boiler, named as in Fröling Connect or by the Fröling scheme ("Heizkreis 01")."""
    info = ChildDeviceInfo(
        identifiers={(DOMAIN, component.identifier(entry.entry_id))},
        parent_device_id=entry.runtime_data.boiler_device_id,
    )
    if (name := connect_name(entry, component)) is not None:
        info["name"] = name
    else:
        info["translation_key"] = DEVICE_TRANSLATION_KEYS[component.kind]
        info["translation_placeholders"] = {"number": f"{component.number:02d}"}
    return info


def device_info(entry: FroelingConfigEntry, component: Component) -> DeviceInfo | ChildDeviceInfo:
    """The boiler device, or a child device of it for all other components."""
    if component.kind is ComponentKind.BOILER:
        return DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})
    return child_device_info(entry, component)


class FroelingEntity(CoordinatorEntity[DataUpdateCoordinator[Any]]):
    """Entity of one component, reading one value key from the source chosen for it.

    `key` names the entity (unique ID, translation); `value_key` is the full library key of the
    value (defaults to the component prefix plus `key`).
    """

    _attr_has_entity_name = True

    def __init__(
        self,
        sources: FroelingSources,
        component: Component,
        key: str,
        source: Source,
        *,
        value_key: str | None = None,
    ) -> None:
        super().__init__(sources.coordinator(source))
        entry = sources.entry
        self.sources = sources
        self.component = component
        self.source = source
        self.value_key = value_key or component.key_prefix + key
        self._followed: list[DataUpdateCoordinator[Any]] = []
        self._required: list[DataUpdateCoordinator[Any]] = []
        self._attr_unique_id = f"{entry.entry_id}_{component.key_prefix}{key}"
        self._attr_device_info = device_info(entry, component)

    def follow(self, coordinator: DataUpdateCoordinator[Any], *, required: bool = True) -> None:
        """Also update with another coordinator; the entity needs a required one to be available.

        Example: the curve sensor needs the outside temperature and the curve settings.
        """
        if coordinator is self.coordinator or coordinator in self._followed:
            return
        self._followed.append(coordinator)
        if required:
            self._required.append(coordinator)

    async def async_added_to_hass(self) -> None:
        """Listen to the followed coordinators as well."""
        await super().async_added_to_hass()
        for coordinator in self._followed:
            self.async_on_remove(coordinator.async_add_listener(self._handle_coordinator_update))

    @callback
    def _handle_coordinator_update(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Available while all required coordinators succeed."""
        return super().available and all(c.last_update_success for c in self._required)

    @property
    def flat_value(self) -> FlatValue:
        """Current value of the entity's key."""
        return self.sources.value(self.value_key)


class FroelingWritableEntity(FroelingEntity):
    """Entity that writes its key; via the cloud only while remote control is allowed."""

    def __init__(
        self,
        sources: FroelingSources,
        component: Component,
        key: str,
        source: Source,
        *,
        value_key: str | None = None,
    ) -> None:
        super().__init__(sources, component, key, source, value_key=value_key)
        self.write_source = sources.write_source(self.value_key)
        if self.write_source is not None:
            self.follow(sources.coordinator(self.write_source))

    @property
    def available(self) -> bool:
        """Writing via Fröling Connect needs 'Fernschalten über connect möglich'."""
        return super().available and (
            self.write_source is not Source.CLOUD or self.sources.remote_control_allowed
        )

    async def async_write(self, value: WriteValue) -> None:
        """Write the entity's key."""
        await self.sources.async_write(self.value_key, value)
