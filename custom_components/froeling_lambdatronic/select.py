"""Select entities of the Fröling Lambdatronic integration."""

from froeling_lambdatronic import (
    AuthenticationError,
    FroelingError,
    OperatingMode,
    RemoteControlDisabledError,
    heating_circuit,
)
from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, HEATING_CIRCUIT_MODE_KEYS, OPERATING_MODE_KEYS
from .coordinator import (
    CloudCoordinator,
    FroelingConfigEntry,
    ParametersCoordinator,
    ValuesCoordinator,
    async_create_remote_control_issue,
)
from .entity import BOILER_COMPONENT, Component, ComponentKind, FroelingEntity, async_write_register

PARALLEL_UPDATES = 1

HEATING_CIRCUIT_MODE = SelectEntityDescription(
    key="operating_mode",
    translation_key="heating_circuit_mode",
    options=list(HEATING_CIRCUIT_MODE_KEYS.values()),
)

SYSTEM_MODE = SelectEntityDescription(
    key="system_operating_mode",
    translation_key="system_operating_mode",
    options=list(OPERATING_MODE_KEYS.values()),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FroelingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the select entities."""
    data = entry.runtime_data
    entities: list[SelectEntity] = [
        FroelingHeatingCircuitModeSelect(data.parameters, Component(ComponentKind.HEATING_CIRCUIT, n))
        for n in data.equipment.heating_circuits
    ]
    if data.cloud is not None:
        entities.append(FroelingSystemModeSelect(data.values, data.cloud))
    async_add_entities(entities)


class FroelingHeatingCircuitModeSelect(FroelingEntity[ParametersCoordinator], SelectEntity):
    """Operating mode of a heating circuit (Modbus)."""

    entity_description = HEATING_CIRCUIT_MODE

    def __init__(self, coordinator: ParametersCoordinator, component: Component) -> None:
        super().__init__(coordinator, component, HEATING_CIRCUIT_MODE.key)
        self._register = heating_circuit(component.number).operating_mode

    @property
    def current_option(self) -> str | None:
        """Current operating mode."""
        mode = self.coordinator.data.heating_circuits[self.component.number].operating_mode
        return None if mode is None else HEATING_CIRCUIT_MODE_KEYS.get(mode)

    async def async_select_option(self, option: str) -> None:
        """Write the operating mode."""
        code = next(code for code, key in HEATING_CIRCUIT_MODE_KEYS.items() if key == option)
        await async_write_register(self.coordinator, self._register, code)


class FroelingSystemModeSelect(FroelingEntity[ValuesCoordinator], SelectEntity):
    """System operating mode: shown from Modbus (34001), switched via Fröling Connect."""

    entity_description = SYSTEM_MODE

    def __init__(self, coordinator: ValuesCoordinator, cloud: CloudCoordinator) -> None:
        super().__init__(coordinator, BOILER_COMPONENT, SYSTEM_MODE.key)
        self._cloud = cloud

    async def async_added_to_hass(self) -> None:
        """Also follow the cloud coordinator."""
        await super().async_added_to_hass()
        self.async_on_remove(self._cloud.async_add_listener(self._handle_cloud_update))

    @callback
    def _handle_cloud_update(self) -> None:
        self.async_write_ha_state()

    @property
    def available(self) -> bool:
        """Only switchable while Fröling Connect works and allows remote control."""
        return (
            super().available
            and self._cloud.last_update_success
            and self._cloud.data is not None
            and self._cloud.data.remote_control_allowed
        )

    @property
    def current_option(self) -> str | None:
        """Current system operating mode as read via Modbus."""
        state = self.coordinator.data.system_state
        return None if state is None else OPERATING_MODE_KEYS.get(state)

    async def async_select_option(self, option: str) -> None:
        """Switch the system operating mode via Fröling Connect."""
        code = next(code for code, key in OPERATING_MODE_KEYS.items() if key == option)
        try:
            await self._cloud.client.set_operating_mode(self._cloud.facility_id, OperatingMode(code))
        except AuthenticationError as err:
            self._cloud.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="cloud_auth_failed") from err
        except RemoteControlDisabledError as err:
            async_create_remote_control_issue(self.hass, self._cloud.config_entry)
            # Reread the permission so that the select becomes unavailable right away.
            await self._cloud.async_request_refresh()
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="remote_control_disabled"
            ) from err
        except FroelingError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cloud_write_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        await self.coordinator.async_request_refresh()
