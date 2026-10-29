"""Diagnostics of the Fröling Lambdatronic integration."""

from dataclasses import asdict
from datetime import time
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import CONF_FACILITY_ID, connection_type
from .coordinator import CloudCoordinator, CloudInfoCoordinator, FroelingConfigEntry

# Fröling Connect also reports the pairing code, address and owner; the library drops them. The
# component names are given by the user in Connect; async_redact_data keeps empty values, so an
# empty standard_name stays ''.
TO_REDACT = {
    CONF_PASSWORD,
    CONF_USERNAME,
    CONF_FACILITY_ID,
    "equipment_number",
    "serial_number",
    "standard_name",
    "display_name",
}
COMPONENT_FIELDS = (
    "component_id",
    "raw_type",
    "number",
    "sub_type",
    "room_sensor",
    "standard_name",
    "display_name",
)


def _coordinator(coordinator: DataUpdateCoordinator[Any]) -> dict[str, Any]:
    return {
        "last_update_success": coordinator.last_update_success,
        "last_exception": None if coordinator.last_exception is None else repr(coordinator.last_exception),
    }


def _modbus(coordinator: DataUpdateCoordinator[Any] | None) -> dict[str, Any] | None:
    if coordinator is None:
        return None
    return {
        **_coordinator(coordinator),
        "raw": None
        if coordinator.data is None
        else {str(k): v for k, v in sorted(coordinator.data.raw.items())},
    }


def _cloud(cloud: CloudCoordinator | None) -> dict[str, Any] | None:
    if cloud is None:
        return None
    result: dict[str, Any] = {
        **_coordinator(cloud),
        "details": cloud.details,
        "skipped": sorted(cloud.skipped),
    }
    if (data := cloud.data) is not None:
        result["values"] = {
            key: value.isoformat() if isinstance(value, time) else value
            for key, value in sorted(data.snapshot.values.items())
        }
        result["components"] = async_redact_data(
            [{key: getattr(c, key) for key in COMPONENT_FIELDS} for c in data.snapshot.components], TO_REDACT
        )
        # As the last refresh tells them (with Modbus from the overview only, None if unknown); not
        # the room sensors in use, which the entry data hold.
        result["room_sensors_snapshot"] = dict(sorted(data.snapshot.room_sensors.items()))
        result["boiler_state"] = asdict(data.boiler_state)
        result["errors"] = [asdict(error) for error in data.errors]
    return result


def _cloud_info(cloud_info: CloudInfoCoordinator | None) -> dict[str, Any] | None:
    if cloud_info is None:
        return None
    result = _coordinator(cloud_info)
    if (data := cloud_info.data) is not None:
        result["system"] = async_redact_data(asdict(data.system), TO_REDACT)
        result["header"] = asdict(data.header)
    return result


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: FroelingConfigEntry
) -> dict[str, Any]:
    """Configuration (redacted), connection type, equipment, source per key, raw and cloud values."""
    data = entry.runtime_data
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "connection_type": connection_type(entry.data),
        "equipment": asdict(data.equipment),
        "sources": data.sources.decisions(),
        "values": _modbus(data.values),
        "parameters": _modbus(data.parameters),
        "cloud": _cloud(data.cloud),
        "cloud_info": _cloud_info(data.cloud_info),
    }
