"""Climate entities (one per heating circuit) of the Fröling Lambdatronic integration."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any

from froeling_lambdatronic import Phase, Weekday, register_for_key
from homeassistant.components.climate import (
    ATTR_TEMPERATURE,
    ClimateEntity,
    ClimateEntityDescription,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import PRECISION_HALVES, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import CONF_ROOM_SENSORS, HEATING_CIRCUIT_MODE_KEYS
from .coordinator import FroelingConfigEntry
from .entity import Component, ComponentKind, FroelingWritableEntity
from .sources import FroelingSources, Source

PARALLEL_UPDATES = 1

HEATING_CIRCUIT = ClimateEntityDescription(key="climate", translation_key="heating_circuit", name=None)

# Operating modes of a heating circuit (48047+ / cloud 'mode').
MODE_OFF = 0
MODE_AUTOMATIC = 1
MODE_EXTRA_HEATING = 2
SETBACK_MODES = frozenset({3, 4})
HVAC_MODES = {HVACMode.OFF: MODE_OFF, HVACMode.AUTO: MODE_AUTOMATIC, HVACMode.HEAT: MODE_EXTRA_HEATING}
# Presets are the modes 1..5; 'off' is the HVAC mode.
PRESETS = {code: key for code, key in HEATING_CIRCUIT_MODE_KEYS.items() if code != MODE_OFF}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FroelingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up a climate entity for every heating circuit whose operating mode can be read and written.

    Which heating circuits have a room sensor only Fröling Connect tells (stored in the entry data);
    Modbus reports 20.0 °C without one.
    """
    data = entry.runtime_data
    sources = data.sources
    room_sensors = entry.data.get(CONF_ROOM_SENSORS, [])
    entities: list[FroelingClimate] = []
    for n in data.equipment.heating_circuits:
        component = Component(ComponentKind.HEATING_CIRCUIT, n)
        key = f"{component.key_prefix}operating_mode"
        if (source := sources.source(key)) is not None and sources.writable(key):
            entities.append(FroelingClimate(sources, component, source, room_sensor=n in room_sensors))
    async_add_entities(entities)


def _in_phase(schedule: Mapping[Weekday, tuple[Phase, ...]], now: datetime) -> bool:
    """True if `now` (local time) lies in a switch-on phase of the schedule."""
    minute = now.hour * 60 + now.minute
    weekday = list(Weekday)[now.weekday()]
    return any(phase.start <= minute < phase.end for phase in schedule.get(weekday, ()))


class FroelingClimate(FroelingWritableEntity, ClimateEntity):
    """Heating circuit: operating mode as HVAC mode and preset, room or flow temperature.

    The target temperature is offered only with a room sensor (known from Fröling Connect): without
    it the room setpoint has no effect. Heating and setback setpoint are Modbus settings (no known
    cloud counterpart). In automatic the schedule from Fröling Connect, if known, tells which of
    both is active.
    """

    entity_description = HEATING_CIRCUIT
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_precision = PRECISION_HALVES
    _attr_target_temperature_step = PRECISION_HALVES

    def __init__(
        self, sources: FroelingSources, component: Component, source: Source, *, room_sensor: bool
    ) -> None:
        super().__init__(
            sources, component, HEATING_CIRCUIT.key, source, value_key=f"{component.key_prefix}operating_mode"
        )
        self._attr_hvac_modes = list(HVAC_MODES)
        self._attr_preset_modes = list(PRESETS.values())
        prefix = component.key_prefix
        self._room_key = f"{prefix}room_temperature"
        self._flow_key = f"{prefix}flow_temperature"
        self._pump_key = f"{prefix}pump"
        self._setpoint_keys = (f"{prefix}room_setpoint_heating", f"{prefix}room_setpoint_setback")
        self._attr_supported_features = (
            ClimateEntityFeature.TURN_OFF | ClimateEntityFeature.TURN_ON | ClimateEntityFeature.PRESET_MODE
        )
        self._room_sensor = room_sensor and all(
            sources.writable(key) and sources.source(key) is not None for key in self._setpoint_keys
        )
        if self._room_sensor:
            self._attr_supported_features |= ClimateEntityFeature.TARGET_TEMPERATURE
            register = register_for_key(self._setpoint_keys[0])
            assert register is not None
            if register.minimum is not None:
                self._attr_min_temp = register.minimum
            if register.maximum is not None:
                self._attr_max_temp = register.maximum
        for key in (self._room_key, self._flow_key, *self._setpoint_keys):
            if (key_source := sources.source(key)) is not None:
                self.follow(sources.coordinator(key_source))
        if (pump_source := sources.source(self._pump_key)) is not None:
            # The pump only adds the HVAC action; the climate entity does not depend on it.
            self.follow(sources.coordinator(pump_source), required=False)

    @property
    def _mode(self) -> int | None:
        mode = self.flat_value
        return mode if isinstance(mode, int) and mode in HEATING_CIRCUIT_MODE_KEYS else None

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Off for mode 'off', heat for 'extra heating', auto otherwise."""
        mode = self._mode
        if mode is None:
            return None
        if mode == MODE_OFF:
            return HVACMode.OFF
        return HVACMode.HEAT if mode == MODE_EXTRA_HEATING else HVACMode.AUTO

    @property
    def preset_mode(self) -> str | None:
        """The operating mode except 'off'."""
        mode = self._mode
        return None if mode is None else PRESETS.get(mode)

    @property
    def hvac_action(self) -> HVACAction | None:
        """Heating or idle from the heating circuit pump, if Fröling Connect reports it."""
        if self._mode == MODE_OFF:
            return HVACAction.OFF
        pump = self.sources.value(self._pump_key)
        if pump is None:
            return None
        return HVACAction.HEATING if pump else HVACAction.IDLE

    @property
    def current_temperature(self) -> float | None:
        """Room temperature with a room sensor, otherwise the flow temperature."""
        value = self.sources.value(self._room_key if self._room_sensor else self._flow_key)
        return float(value) if isinstance(value, float | int) else None

    @property
    def target_temperature(self) -> float | None:
        """Room setpoint of the active mode (setback setpoint while setting back); only with a room sensor."""
        value = self.sources.value(self._setpoint_key)
        return float(value) if isinstance(value, float | int) else None

    @property
    def _setpoint_key(self) -> str:
        """Setback setpoint while setting back; in automatic outside the phases of a known schedule."""
        heating, setback = self._setpoint_keys
        mode = self._mode
        if mode in SETBACK_MODES:
            return setback
        if mode == MODE_AUTOMATIC and (schedule := self.sources.schedule(self.component.key)) is not None:
            return heating if _in_phase(schedule, dt_util.now()) else setback
        return heating

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Write off, automatic or extra heating."""
        await self.async_write(HVAC_MODES[hvac_mode])

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Write the operating mode of the preset."""
        await self.async_write(next(code for code, key in PRESETS.items() if key == preset_mode))

    async def async_turn_on(self) -> None:
        """Switch to automatic."""
        await self.async_write(MODE_AUTOMATIC)

    async def async_turn_off(self) -> None:
        """Switch the heating circuit off."""
        await self.async_write(MODE_OFF)

    async def async_set_temperature(self, **kwargs: Any) -> None:  # noqa: ANN401 - ClimateEntity signature
        """Write the room setpoint of the active mode (offered only with a room sensor)."""
        await self.sources.async_write(self._setpoint_key, kwargs[ATTR_TEMPERATURE])
