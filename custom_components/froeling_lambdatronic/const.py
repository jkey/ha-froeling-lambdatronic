"""Constants of the Fröling Lambdatronic integration."""

from collections.abc import Mapping
from datetime import timedelta
from enum import StrEnum
from typing import Any, Final

from homeassistant.const import CONF_HOST

DOMAIN: Final = "froeling_lambdatronic"

DEFAULT_PORT: Final = 502
DEFAULT_SLAVE: Final = 2

CONF_SLAVE: Final = "slave"
CONF_EQUIPMENT: Final = "equipment"
CONF_HEATING_CIRCUITS: Final = "heating_circuits"
CONF_HOT_WATER: Final = "hot_water"
CONF_BUFFERS: Final = "buffers"
CONF_CIRCULATION_PUMP: Final = "circulation_pump"
CONF_SCAN_INTERVAL: Final = "scan_interval"
CONF_PARAMETER_INTERVAL: Final = "parameter_interval"
CONF_CLOUD_INTERVAL: Final = "cloud_interval"
CONF_CLOUD: Final = "cloud"
CONF_FACILITY_ID: Final = "facility_id"

DEFAULT_SCAN_INTERVAL: Final = 30
MIN_SCAN_INTERVAL: Final = 10
MAX_SCAN_INTERVAL: Final = 300
DEFAULT_PARAMETER_INTERVAL: Final = 5
MIN_PARAMETER_INTERVAL: Final = 1
MAX_PARAMETER_INTERVAL: Final = 60
DEFAULT_CLOUD_INTERVAL: Final = 2
MIN_CLOUD_INTERVAL: Final = 1
MAX_CLOUD_INTERVAL: Final = 60
CLOUD_INFO_INTERVAL: Final = timedelta(hours=1)

MANUFACTURER: Final = "Fröling"
MODEL: Final = "Lambdatronic 3200"

ISSUE_PROTOCOL_DISABLED: Final = "protocol_2014_disabled"
ISSUE_REMOTE_CONTROL_DISABLED: Final = "remote_control_disabled"


class ConnectionType(StrEnum):
    """How the integration talks to the boiler."""

    MODBUS = "modbus"
    CLOUD = "cloud"
    HYBRID = "hybrid"


def connection_type(data: Mapping[str, Any]) -> ConnectionType:
    """Connection type of a config entry, derived from its data (Modbus host and/or Connect login)."""
    modbus = CONF_HOST in data
    cloud = data.get(CONF_CLOUD) is not None
    if modbus and cloud:
        return ConnectionType.HYBRID
    return ConnectionType.MODBUS if modbus else ConnectionType.CLOUD


# Register 34001 (system state) -> translation key.
SYSTEM_STATE_KEYS: Final[dict[int, str]] = {
    0: "continuous_load",
    1: "domestic_hot_water",
    2: "automatic",
    3: "firewood",
    4: "cleaning",
    5: "off",
    6: "extra_heating",
    7: "chimney_sweep",
    8: "cleaning_2",
}

# Register 34002 (boiler state) -> translation key.
BOILER_STATE_KEYS: Final[dict[int, str]] = {
    0: "fault",
    1: "boiler_off",
    2: "heating_up",
    3: "heating",
    4: "fire_maintenance",
    5: "fire_out",
    6: "door_open",
    7: "preparation",
    8: "warming_up",
    9: "ignition",
    10: "shutdown_wait",
    11: "shutdown_wait_1",
    12: "shutdown_feed_1",
    13: "shutdown_wait_2",
    14: "shutdown_feed_2",
    15: "cleaning",
    16: "wait_2h",
    17: "suction_heating",
    18: "ignition_failure",
    19: "ready",
    20: "grate_closing",
    21: "stoker_emptying",
    22: "preheating",
    23: "suction",
    24: "rse_closing",
    25: "rse_opening",
    26: "grate_tilting",
    27: "warming_up_ignition",
    28: "residual_feed",
    29: "stoker_filling",
    30: "lambda_probe_heating",
    31: "fan_overrun_1",
    32: "fan_overrun_2",
    33: "stopped",
    34: "reignition",
    35: "ignition_wait",
    36: "recovery_rse_closing",
    37: "recovery_boiler_venting",
    38: "recovery_ignition",
    39: "recovery_min_feed",
    40: "rse_closing_2",
    41: "fault_stb_na",
    42: "fault_tilting_grate",
    43: "fault_overpressure",
    44: "fault_door_contact",
    45: "fault_induced_draft",
    46: "fault_environment",
    47: "error_stb_na",
    48: "error_tilting_grate",
    49: "error_overpressure",
    50: "error_door_contact",
    51: "error_induced_draft",
    52: "error_environment",
    53: "error_stoker",
    54: "fault_stoker",
    55: "recovery_stoker_emptying",
    56: "pre_ventilation",
    57: "fault_wood_chips",
    58: "error_wood_chips",
    59: "nb_door_open",
    60: "nb_heating_up",
    61: "nb_heating",
    62: "error_stb_na_2",
    63: "error_general",
    64: "nb_fire_out",
    65: "self_test",
    66: "error_handling_20min",
    67: "error_drop_shaft",
    68: "fault_drop_shaft",
    69: "cleaning_possible",
    70: "heating_cleaning",
    71: "firewood_heating_up",
    72: "firewood_heating",
    73: "firewood_heating_shutdown",
    74: "fault_safe",
    75: "egr_overrun",
    76: "egr_cleaning",
    77: "ignition_off",
    78: "filter_cleaning",
    79: "heating_up_assistant",
    80: "firewood_ignition",
    81: "firewood_fault",
    82: "sensor_check",
}

# Heating circuit operating mode (48047+) <-> translation key.
HEATING_CIRCUIT_MODE_KEYS: Final[dict[int, str]] = {
    0: "off",
    1: "automatic",
    2: "extra_heating",
    3: "setback",
    4: "permanent_setback",
    5: "party",
}

# System operating mode switchable via Fröling Connect (values of 34001).
OPERATING_MODE_KEYS: Final[dict[int, str]] = {
    0: "continuous_load",
    1: "domestic_hot_water",
    2: "automatic",
}

# Operating mode of a hot water tank, only in Fröling Connect ('mode' of the DHW component).
HOT_WATER_MODE_KEYS: Final[dict[int, str]] = {
    1: "auto",
    2: "extra_charge",
}
