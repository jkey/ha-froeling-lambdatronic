"""Water heater entities (one per hot water tank) of the Fröling Lambdatronic integration."""

from typing import Any

from froeling_lambdatronic import register_for_key
from homeassistant.components.water_heater import (
    ATTR_TEMPERATURE,
    WaterHeaterEntity,
    WaterHeaterEntityDescription,
    WaterHeaterEntityFeature,
)
from homeassistant.const import PRECISION_HALVES, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import HOT_WATER_MODE_KEYS
from .coordinator import FroelingConfigEntry
from .entity import Component, ComponentKind, FroelingWritableEntity
from .sources import FroelingSources, Source

PARALLEL_UPDATES = 1

HOT_WATER = WaterHeaterEntityDescription(key="water_heater", translation_key="hot_water", name=None)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FroelingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a water heater for every hot water tank whose setpoint can be read and written."""
    data = entry.runtime_data
    sources = data.sources
    entities: list[FroelingWaterHeater] = []
    for n in data.equipment.hot_water:
        component = Component(ComponentKind.HOT_WATER, n)
        key = f"{component.key_prefix}setpoint"
        if (source := sources.source(key)) is not None and sources.writable(key):
            entities.append(FroelingWaterHeater(sources, component, source))
    async_add_entities(entities)


class FroelingWaterHeater(FroelingWritableEntity, WaterHeaterEntity):
    """Hot water tank: temperature at the top, setpoint and, with Fröling Connect, the operating mode."""

    entity_description = HOT_WATER
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = PRECISION_HALVES
    _attr_target_temperature_step = PRECISION_HALVES

    def __init__(self, sources: FroelingSources, component: Component, source: Source) -> None:
        prefix = component.key_prefix
        super().__init__(sources, component, HOT_WATER.key, source, value_key=f"{prefix}setpoint")
        self._temperature_key = f"{prefix}temperature"
        self._mode_key = f"{prefix}mode"
        self._attr_supported_features = WaterHeaterEntityFeature.TARGET_TEMPERATURE
        # Limits of the write path, as for the number entities.
        register = register_for_key(self.value_key)
        assert register is not None
        minimum, maximum = register.minimum, register.maximum
        if self.write_source is Source.CLOUD and (limits := sources.cloud_limits(self.value_key)) is not None:
            minimum, maximum = limits
        if minimum is not None:
            self._attr_min_temp = minimum
        if maximum is not None:
            self._attr_max_temp = maximum
        if (temperature_source := sources.source(self._temperature_key)) is not None:
            self.follow(sources.coordinator(temperature_source))
        # Auto/extra charge exists only in Fröling Connect (41631 does not match).
        self._mode_coordinator: DataUpdateCoordinator[Any] | None = None
        if (mode_source := sources.source(self._mode_key)) is not None and sources.writable(self._mode_key):
            self._attr_supported_features |= WaterHeaterEntityFeature.OPERATION_MODE
            self._attr_operation_list = list(HOT_WATER_MODE_KEYS.values())
            self._mode_coordinator = sources.coordinator(mode_source)
            # With Modbus the mode only adds information; a Connect outage must not hide the tank.
            self.follow(self._mode_coordinator, required=False)

    @property
    def current_temperature(self) -> float | None:
        """Temperature at the top of the tank."""
        value = self.sources.value(self._temperature_key)
        return float(value) if isinstance(value, float | int) else None

    @property
    def target_temperature(self) -> float | None:
        """Setpoint of the tank."""
        value = self.flat_value
        return float(value) if isinstance(value, float | int) else None

    @property
    def current_operation(self) -> str | None:
        """Automatic or extra charge; None without Fröling Connect or while it fails (no stale mode)."""
        if self._mode_coordinator is None or not self._mode_coordinator.last_update_success:
            return None
        mode = self.sources.value(self._mode_key)
        return HOT_WATER_MODE_KEYS.get(mode) if isinstance(mode, int) else None

    async def async_set_temperature(self, **kwargs: Any) -> None:  # noqa: ANN401 - WaterHeaterEntity signature
        """Write the setpoint."""
        await self.async_write(kwargs[ATTR_TEMPERATURE])

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        """Write the operating mode via Fröling Connect."""
        code = next(code for code, key in HOT_WATER_MODE_KEYS.items() if key == operation_mode)
        await self.sources.async_write(self._mode_key, code)
