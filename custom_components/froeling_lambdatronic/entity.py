"""Base entity, devices and the shared write path of the Fröling Lambdatronic integration."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, cast

from froeling_lambdatronic import (
    Equipment,
    FroelingError,
    ProtocolNotEnabledError,
    RegisterDef,
    ValueOutOfRangeError,
    WriteRejectedError,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.device_registry import ChildDeviceInfo, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator

from .const import DOMAIN, ISSUE_PROTOCOL_DISABLED
from .coordinator import FroelingConfigEntry, ParametersCoordinator


class ComponentKind(StrEnum):
    """Kind of a device; the value is the key prefix used by the library (e.g. 'hc' in 'hc1_setback')."""

    BOILER = "boiler"
    HEATING_CIRCUIT = "hc"
    HOT_WATER = "dhw"
    BUFFER = "buffer"


_DEVICE_TRANSLATION_KEYS = {
    ComponentKind.HEATING_CIRCUIT: "heating_circuit",
    ComponentKind.HOT_WATER: "hot_water",
    ComponentKind.BUFFER: "buffer",
}


@dataclass(frozen=True, slots=True)
class Component:
    """The boiler itself (number 0) or one heating circuit, hot water tank or buffer."""

    kind: ComponentKind
    number: int = 0

    @property
    def key_prefix(self) -> str:
        """Prefix of entity keys, '' for the boiler and e.g. 'hc1_' for heating circuit 1."""
        return "" if self.kind is ComponentKind.BOILER else f"{self.kind}{self.number}_"

    def identifier(self, entry_id: str) -> str:
        """Device registry identifier."""
        return entry_id if self.kind is ComponentKind.BOILER else f"{entry_id}_{self.kind}{self.number}"


BOILER_COMPONENT = Component(ComponentKind.BOILER)


def components(equipment: Equipment) -> list[Component]:
    """All devices for the given equipment, the boiler first."""
    return [
        BOILER_COMPONENT,
        *(Component(ComponentKind.HEATING_CIRCUIT, n) for n in equipment.heating_circuits),
        *(Component(ComponentKind.HOT_WATER, n) for n in equipment.hot_water),
        *(Component(ComponentKind.BUFFER, n) for n in equipment.buffers),
    ]


def device_info(entry: FroelingConfigEntry, component: Component) -> DeviceInfo | ChildDeviceInfo:
    """The boiler device, or a child device of it for heating circuits, tanks and buffers."""
    if component.kind is ComponentKind.BOILER:
        return DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})
    return ChildDeviceInfo(
        identifiers={(DOMAIN, component.identifier(entry.entry_id))},
        parent_device_id=entry.runtime_data.boiler_device_id,
        translation_key=_DEVICE_TRANSLATION_KEYS[component.kind],
        translation_placeholders={"number": str(component.number)},
    )


class FroelingEntity[CoordinatorT: DataUpdateCoordinator[Any]](CoordinatorEntity[CoordinatorT]):
    """Entity bound to one component and one coordinator."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: CoordinatorT, component: Component, key: str) -> None:
        super().__init__(coordinator)
        entry = cast(FroelingConfigEntry, coordinator.config_entry)
        self.component = component
        self._attr_unique_id = f"{entry.entry_id}_{component.key_prefix}{key}"
        self._attr_device_info = device_info(entry, component)


async def async_write_register(
    coordinator: ParametersCoordinator, register: RegisterDef, value: float
) -> None:
    """Write one setting, translate library errors and refresh the parameters."""
    entry = coordinator.config_entry
    issue_id = f"{ISSUE_PROTOCOL_DISABLED}_{entry.entry_id}"
    try:
        await coordinator.client.write(register, value)
    except ValueOutOfRangeError as err:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="value_out_of_range",
            translation_placeholders={
                "value": str(value),
                "minimum": str(err.minimum),
                "maximum": str(err.maximum),
            },
        ) from err
    except ProtocolNotEnabledError as err:
        ir.async_create_issue(
            coordinator.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_PROTOCOL_DISABLED,
            translation_placeholders={"name": entry.title},
        )
        raise HomeAssistantError(translation_domain=DOMAIN, translation_key="protocol_disabled") from err
    except WriteRejectedError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="write_rejected",
            translation_placeholders={"reason": err.reason},
        ) from err
    except FroelingError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="communication_failed",
            translation_placeholders={"error": str(err)},
        ) from err
    ir.async_delete_issue(coordinator.hass, DOMAIN, issue_id)
    await coordinator.async_request_refresh()
