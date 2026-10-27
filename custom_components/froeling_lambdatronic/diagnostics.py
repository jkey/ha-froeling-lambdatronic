"""Diagnostics of the Fröling Lambdatronic integration."""

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import CONF_FACILITY_ID
from .coordinator import FroelingConfigEntry

TO_REDACT = {CONF_PASSWORD, CONF_USERNAME, CONF_FACILITY_ID}


def _coordinator(coordinator: DataUpdateCoordinator[Any] | None) -> dict[str, Any] | None:
    if coordinator is None:
        return None
    return {
        "last_update_success": coordinator.last_update_success,
        "last_exception": None if coordinator.last_exception is None else repr(coordinator.last_exception),
        "raw": None
        if coordinator.data is None
        else {str(k): v for k, v in sorted(coordinator.data.raw.items())},
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: FroelingConfigEntry
) -> dict[str, Any]:
    """Configuration (redacted), equipment, raw registers and last errors."""
    data = entry.runtime_data
    values = data.values
    parameters = data.parameters
    result: dict[str, Any] = {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "equipment": asdict(data.equipment),
        "values": _coordinator(values),
        "parameters": _coordinator(parameters),
        "cloud": None,
    }
    if data.cloud is not None:
        result["cloud"] = {
            "last_update_success": data.cloud.last_update_success,
            "state": None if data.cloud.data is None else asdict(data.cloud.data.boiler_state),
        }
    return result
