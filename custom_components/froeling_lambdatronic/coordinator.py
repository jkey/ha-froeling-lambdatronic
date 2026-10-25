"""Data update coordinators of the Fröling Lambdatronic integration."""

import logging
from dataclasses import dataclass
from datetime import timedelta

from froeling_lambdatronic import (
    AuthenticationError,
    CloudBoilerState,
    Equipment,
    FroelingConnectClient,
    FroelingError,
    LambdatronicClient,
    LambdatronicParameters,
    LambdatronicValues,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CLOUD_INTERVAL,
    CONF_PARAMETER_INTERVAL,
    CONF_SCAN_INTERVAL,
    DEFAULT_PARAMETER_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ISSUE_REMOTE_CONTROL_DISABLED,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class FroelingRuntimeData:
    """Objects shared by all platforms of one config entry."""

    client: LambdatronicClient
    equipment: Equipment
    boiler_device_id: str
    values: ValuesCoordinator
    parameters: ParametersCoordinator
    cloud: CloudCoordinator | None


type FroelingConfigEntry = ConfigEntry[FroelingRuntimeData]


class ValuesCoordinator(DataUpdateCoordinator[LambdatronicValues]):
    """Polls the current values (Werte-Takt)."""

    config_entry: FroelingConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: FroelingConfigEntry,
        client: LambdatronicClient,
        equipment: Equipment,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} values",
            update_interval=timedelta(seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
        )
        self.client = client
        self.equipment = equipment

    async def _async_update_data(self) -> LambdatronicValues:
        try:
            return await self.client.read_values(self.equipment)
        except FroelingError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err


class ParametersCoordinator(DataUpdateCoordinator[LambdatronicParameters]):
    """Polls the writable settings (Parameter-Takt) and after every write."""

    config_entry: FroelingConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: FroelingConfigEntry,
        client: LambdatronicClient,
        equipment: Equipment,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} parameters",
            update_interval=timedelta(
                minutes=entry.options.get(CONF_PARAMETER_INTERVAL, DEFAULT_PARAMETER_INTERVAL)
            ),
        )
        self.client = client
        self.equipment = equipment

    async def _async_update_data(self) -> LambdatronicParameters:
        try:
            return await self.client.read_parameters(self.equipment)
        except FroelingError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err


class CloudCoordinator(DataUpdateCoordinator[CloudBoilerState]):
    """Reads operating mode and remote control permission from Fröling Connect."""

    config_entry: FroelingConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: FroelingConfigEntry,
        client: FroelingConnectClient,
        facility_id: int,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} cloud",
            update_interval=CLOUD_INTERVAL,
        )
        self.client = client
        self.facility_id = facility_id

    async def _async_update_data(self) -> CloudBoilerState:
        try:
            state = await self.client.get_boiler_state(self.facility_id)
        except AuthenticationError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="cloud_auth_failed"
            ) from err
        except FroelingError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="cloud_update_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        if state.remote_control_allowed:
            ir.async_delete_issue(
                self.hass, DOMAIN, f"{ISSUE_REMOTE_CONTROL_DISABLED}_{self.config_entry.entry_id}"
            )
        else:
            async_create_remote_control_issue(self.hass, self.config_entry)
        return state


def async_create_remote_control_issue(hass: HomeAssistant, entry: FroelingConfigEntry) -> None:
    """Tell the user to enable 'Fernschalten über connect möglich'."""
    ir.async_create_issue(
        hass,
        DOMAIN,
        f"{ISSUE_REMOTE_CONTROL_DISABLED}_{entry.entry_id}",
        is_fixable=False,
        severity=ir.IssueSeverity.WARNING,
        translation_key=ISSUE_REMOTE_CONTROL_DISABLED,
        translation_placeholders={"name": entry.title},
    )
