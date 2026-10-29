"""Choice of the source per value (Modbus or Fröling Connect) and the shared write path."""

from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import time
from enum import StrEnum
from typing import Any

from froeling_lambdatronic import (
    AuthenticationError,
    CloudComponentDetail,
    CloudParameterNotFoundError,
    FlatValue,
    FroelingConnectClient,
    FroelingError,
    LambdatronicClient,
    Phase,
    ProtocolNotEnabledError,
    RemoteControlDisabledError,
    ValueOutOfRangeError,
    Weekday,
    WriteRejectedError,
    cloud_ref,
    component_key,
    register_for_key,
    time_to_minutes,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import CONF_CLOUD_KEYS, DOMAIN, ISSUE_PROTOCOL_DISABLED
from .coordinator import (
    CloudCoordinator,
    CloudData,
    CloudInfoCoordinator,
    FroelingConfigEntry,
    ModbusParametersCoordinator,
    ModbusValuesCoordinator,
    async_create_remote_control_issue,
)

type WriteValue = float | time
"""A value in library units; `float` includes `int` and `bool` (e.g. 'auto_feeder_off')."""


class Source(StrEnum):
    """Where a value is read from or written to.

    `CLOUD_INFO` (system information and connection state) holds no value keys; entities that
    show it use the source only to find their coordinator.
    """

    MODBUS_VALUES = "modbus_values"
    MODBUS_PARAMETERS = "modbus_parameters"
    CLOUD = "cloud"
    CLOUD_INFO = "cloud_info"


class FroelingSources:
    """The only place that decides between Modbus and Fröling Connect.

    Reading: Modbus wins; the cloud provides only values Modbus does not have. The source of a
    key is decided once (during setup, after the first refresh) and never switched afterwards.
    Writing: via Modbus if the key has a writable register, otherwise via the cloud.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: FroelingConfigEntry,
        *,
        modbus: LambdatronicClient | None,
        values: ModbusValuesCoordinator | None,
        parameters: ModbusParametersCoordinator | None,
        cloud_client: FroelingConnectClient | None,
        cloud: CloudCoordinator | None,
        cloud_info: CloudInfoCoordinator | None,
        facility_id: int | None,
    ) -> None:
        self._hass = hass
        self.entry = entry
        self._modbus = modbus
        self._values = values
        self._parameters = parameters
        self._cloud_client = cloud_client
        self._cloud = cloud
        self._cloud_info = cloud_info
        self._facility_id = facility_id
        self._decided: dict[str, Source | None] = {}
        self._flat_cache: dict[Source, tuple[object, Mapping[str, FlatValue]]] = {}

    def source(self, key: str) -> Source | None:
        """Source of a full key such as 'hc1_flow_temperature'; None if no source has it."""
        if key not in self._decided:
            self._decided[key] = self._decide(key)
        return self._decided[key]

    def decisions(self) -> dict[str, Source | None]:
        """Every key asked for so far and its source (for diagnostics)."""
        return dict(sorted(self._decided.items()))

    def _decide(self, key: str) -> Source | None:
        if self._values is not None and key in self._flat(Source.MODBUS_VALUES):
            return Source.MODBUS_VALUES
        if self._parameters is not None and key in self._flat(Source.MODBUS_PARAMETERS):
            return Source.MODBUS_PARAMETERS
        if self._cloud is not None and self._cloud_has(key, self._cloud):
            return Source.CLOUD
        return None

    def _cloud_has(self, key: str, cloud: CloudCoordinator) -> bool:
        if cloud.data is not None and key in cloud.data.snapshot.values:
            return True
        # Not read: the first cloud update failed, or the device of the component is disabled
        # and therefore skipped. The catalogue decides, so that the entity exists (unavailable
        # or disabled) and appears once the cloud answers or the device is enabled again. After
        # a failed first update the keys of an earlier overview narrow the catalogue down.
        if cloud_ref(key) is None:
            return False
        if cloud.data is None:
            known: list[str] | None = self.entry.data.get(CONF_CLOUD_KEYS)
            return known is None or key in known
        return key.split("_", 1)[0] in cloud.skipped

    def coordinator(self, source: Source) -> DataUpdateCoordinator[Any]:
        """Coordinator of a source."""
        coordinator: DataUpdateCoordinator[Any] | None = {
            Source.MODBUS_VALUES: self._values,
            Source.MODBUS_PARAMETERS: self._parameters,
            Source.CLOUD: self._cloud,
            Source.CLOUD_INFO: self._cloud_info,
        }[source]
        if coordinator is None:
            raise ValueError(f"source {source} is not configured")
        return coordinator

    def value(self, key: str) -> FlatValue:
        """Current value of a key from its source; None if unknown."""
        source = self.source(key)
        if source is None:
            return None
        if source is Source.CLOUD:
            data = self._cloud_data()
            return None if data is None else data.snapshot.value(key)
        return self._flat(source).get(key)

    def _cloud_data(self) -> CloudData | None:
        """Data of the last cloud update; None while the cloud fails (beyond its tolerance)."""
        cloud = self._cloud
        return None if cloud is None or not cloud.last_update_success else cloud.data

    def _flat(self, source: Source) -> Mapping[str, FlatValue]:
        """Values of a Modbus coordinator under full keys, computed once per update."""
        data = self.coordinator(source).data
        cached = self._flat_cache.get(source)
        if cached is None or cached[0] is not data:
            cached = (data, data.flat())
            self._flat_cache[source] = cached
        return cached[1]

    def schedule(self, component_key: str) -> Mapping[Weekday, tuple[Phase, ...]] | None:
        """Weekly schedule of a component ('hc1') from the last cloud update; None if unknown."""
        data = self._cloud_data()
        return None if data is None else data.snapshot.schedules.get(component_key)

    def cloud_limits(self, key: str) -> tuple[float | None, float | None] | None:
        """Minimum and maximum of the cloud parameter of a key in library units; None if not read."""
        cloud = self._cloud
        resolved = cloud_ref(key)
        if cloud is None or cloud.data is None or resolved is None:
            return None
        ref, number = resolved
        detail = cloud.data.details.get(component_key(ref.component, number))
        if detail is None or (parameter := detail.parameters.get(ref.name)) is None:
            return None
        return (
            None if parameter.minimum is None else parameter.minimum * ref.factor,
            None if parameter.maximum is None else parameter.maximum * ref.factor,
        )

    @property
    def remote_control_allowed(self) -> bool:
        """True while Fröling Connect answers and allows remote control."""
        cloud = self._cloud
        return (
            cloud is not None
            and cloud.last_update_success
            and cloud.data is not None
            and cloud.data.remote_control_allowed
        )

    def write_source(self, key: str) -> Source | None:
        """Where a key is written: a writable Modbus register first, then the cloud parameter.

        The cloud parameters of settings are editable; `set_value` checks the flag on writing.
        """
        register = register_for_key(key)
        if self._modbus is not None and register is not None and register.writable:
            return Source.MODBUS_PARAMETERS
        if self._cloud is not None and cloud_ref(key) is not None:
            return Source.CLOUD
        return None

    def writable(self, key: str) -> bool:
        """True if there is a way to write the key."""
        return self.write_source(key) is not None

    async def async_write(self, key: str, value: WriteValue) -> None:
        """Write a value, translate library errors and read the used sources again."""
        source = self.write_source(key)
        if source is Source.MODBUS_PARAMETERS:
            await self._async_write_modbus(key, value)
        elif source is Source.CLOUD:
            await self._async_write_cloud(key, value)
        else:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="not_writable",
                translation_placeholders={"key": key},
            )
        await self.coordinator(source).async_request_refresh()
        read_source = self.source(key)
        if read_source is not None and read_source is not source:
            await self.coordinator(read_source).async_request_refresh()

    async def _async_write_modbus(self, key: str, value: WriteValue) -> None:
        assert self._modbus is not None
        register = register_for_key(key)
        assert register is not None
        raw = time_to_minutes(value) if isinstance(value, time) else float(value)
        issue_id = f"{ISSUE_PROTOCOL_DISABLED}_{self.entry.entry_id}"
        try:
            await self._modbus.write(register, raw)
        except ValueOutOfRangeError as err:
            raise _out_of_range(value, err) from err
        except ProtocolNotEnabledError as err:
            ir.async_create_issue(
                self._hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.ERROR,
                translation_key=ISSUE_PROTOCOL_DISABLED,
                translation_placeholders={"name": self.entry.title},
            )
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="protocol_disabled") from err
        except WriteRejectedError as err:
            raise _rejected(err) from err
        except FroelingError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="communication_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        ir.async_delete_issue(self._hass, DOMAIN, issue_id)

    async def _async_write_cloud(self, key: str, value: WriteValue) -> None:
        assert self._cloud_client is not None
        assert self._facility_id is not None
        async with self._cloud_errors(value):
            await self._cloud_client.set_value(self._facility_id, key, value)

    async def async_get_schedule(self, component_key: str) -> CloudComponentDetail:
        """Detail of a component ('hc1', 'dhw1') read from Fröling Connect.

        It carries the weekly schedule (`schedule`) and the days whose phases could not be read
        (`skipped_phase_days`). A component without all seven days has no weekly schedule, as
        for writing.
        """
        client, facility_id = self._schedule_client()
        async with self._cloud_errors(None, failed_key="cloud_update_failed"):
            components = await client.get_components(facility_id)
            component = next((c for c in components if c.key == component_key), None)
            if component is None:
                raise _no_schedule(component_key)
            detail = await client.get_component_detail(facility_id, component.component_id)
        if set(detail.schedule_ids) != set(Weekday):
            raise _no_schedule(component_key)
        return detail

    async def async_set_schedule(
        self, component_key: str, changes: Mapping[Weekday, Sequence[Phase]]
    ) -> None:
        """Replace the phases of the given days and read the cloud again."""
        client, facility_id = self._schedule_client()
        async with self._cloud_errors(None):
            try:
                await client.set_schedule(facility_id, component_key, changes)
            except CloudParameterNotFoundError as err:
                raise _no_schedule(component_key) from err
        await self.coordinator(Source.CLOUD).async_request_refresh()

    def _schedule_client(self) -> tuple[FroelingConnectClient, int]:
        """Schedules exist only in Fröling Connect."""
        if self._cloud_client is None or self._facility_id is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="schedule_requires_connect"
            )
        return self._cloud_client, self._facility_id

    @asynccontextmanager
    async def _cloud_errors(
        self, value: WriteValue | None, *, failed_key: str = "cloud_write_failed"
    ) -> AsyncIterator[None]:
        """Translate the errors of a Fröling Connect request; fix login and permission problems."""
        try:
            yield
        except ValueOutOfRangeError as err:
            raise _out_of_range(value, err) from err
        except AuthenticationError as err:
            self.entry.async_start_reauth(self._hass)
            raise HomeAssistantError(translation_domain=DOMAIN, translation_key="cloud_auth_failed") from err
        except RemoteControlDisabledError as err:
            async_create_remote_control_issue(self._hass, self.entry)
            # Read the permission again so that the cloud entities become unavailable at once.
            await self.coordinator(Source.CLOUD).async_request_refresh()
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="remote_control_disabled"
            ) from err
        except WriteRejectedError as err:
            raise _rejected(err) from err
        except FroelingError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=failed_key,
                translation_placeholders={"error": str(err)},
            ) from err


def _out_of_range(value: WriteValue | None, err: ValueOutOfRangeError) -> ServiceValidationError:
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="value_out_of_range",
        translation_placeholders={
            "value": str(value),
            "minimum": str(err.minimum),
            "maximum": str(err.maximum),
        },
    )


def _no_schedule(component_key: str) -> ServiceValidationError:
    return ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="no_schedule",
        translation_placeholders={"component": component_key},
    )


def _rejected(err: WriteRejectedError) -> HomeAssistantError:
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="write_rejected",
        translation_placeholders={"reason": err.reason},
    )
