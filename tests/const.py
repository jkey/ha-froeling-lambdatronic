"""Test data: Modbus values of the real register snapshot of 24.10.2026, anonymised cloud fixtures."""

import json
from collections.abc import Iterable
from dataclasses import replace
from datetime import time
from pathlib import Path
from typing import Any

from froeling_lambdatronic import (
    BufferParameters,
    BufferValues,
    CirculationValues,
    CloudBoilerState,
    CloudComponent,
    CloudOverview,
    Equipment,
    HeatingCircuitParameters,
    HeatingCircuitValues,
    HotWaterParameters,
    HotWaterValues,
    LambdatronicParameters,
    LambdatronicValues,
    OperatingMode,
)
from froeling_lambdatronic.cloud_models import (
    parse_component_detail,
    parse_component_list,
    parse_errors,
    parse_header,
    parse_overview,
    parse_system_info,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME

from custom_components.froeling_lambdatronic.const import (
    CONF_BUFFERS,
    CONF_CIRCULATION_PUMP,
    CONF_CLOUD,
    CONF_CLOUD_INTERVAL,
    CONF_EQUIPMENT,
    CONF_FACILITY_ID,
    CONF_HEATING_CIRCUITS,
    CONF_HOT_WATER,
    CONF_PARAMETER_INTERVAL,
    CONF_ROOM_SENSORS,
    CONF_SCAN_INTERVAL,
    CONF_SLAVE,
    ConnectionType,
)

ENTRY_ID = "01K8FROELING0TEST0ENTRY000"
HOST = "192.168.1.50"
UNIQUE_ID = f"{HOST}:502:2"
FACILITY_ID = 777
CLOUD_UNIQUE_ID = f"cloud:{FACILITY_ID}"
USERNAME = "kessel@example.com"
PASSWORD = "geheim"

EQUIPMENT = Equipment(heating_circuits=(1, 2), hot_water=(1,), buffers=(1,), circulation_pump=True)

VALUES = LambdatronicValues(
    boiler_temperature=63.0,
    flue_gas_temperature=61.0,
    residual_oxygen=21.0,
    induced_draft_speed=0.0,
    return_temperature=0.0,
    boiler_control_output=32.0,
    operating_hours=17697.0,
    hours_since_maintenance=17702.0,
    pellet_hopper_level=100.0,
    burner_starts=10473.0,
    feed_rate=0.0,
    boiler_setpoint_calculated=70.0,
    heating_hours=12835.0,
    pellet_counter=1094.0,
    pellet_consumption_total=42.9,
    hours_until_ash_emptying=236.0,
    outside_temperature=9.5,
    boiler_demand=False,
    system_state=2,
    boiler_state=19,
    errors=(),
    heating_circuits={
        1: HeatingCircuitValues(flow_temperature=40.5, flow_setpoint=0.0, room_temperature=20.0),
        2: HeatingCircuitValues(flow_temperature=32.0, flow_setpoint=0.0, room_temperature=20.0),
    },
    hot_water={1: HotWaterValues(temperature=51.0, pump=0.0)},
    buffers={1: BufferValues(top=56.5, middle=None, bottom=50.0, pump=30.0)},
    circulation=CirculationValues(flow_switch=False, pump=0.0, return_temperature=None),
    raw={30001: 126, 34001: 2, 34002: 19},
)


def _circuit(
    plus10: float, minus10: float, setback: float, max_flow: float, limit: float
) -> HeatingCircuitParameters:
    return HeatingCircuitParameters(
        curve_flow_plus10=plus10,
        curve_flow_minus10=minus10,
        setback=setback,
        max_flow=max_flow,
        heating_limit=limit,
        heating_limit_setback=7.0,
        operating_mode=1,
        room_setpoint_heating=20.0,
        room_setpoint_setback=17.0,
        frost_protection=10.0,
        pump_off_below_flow_setpoint=20.0,
        buffer_overheat_protection=120.0,
    )


PARAMETERS = LambdatronicParameters(
    boiler_setpoint=70.0,
    pellet_stock=4.5,
    pellet_stock_minimum=0.0,
    pellet_filling_start_1=time(10, 0),
    pellet_filling_start_2=time(22, 0),
    auto_feeder_off=False,
    circulation_pump_off_above=35.0,
    heating_circuits={1: _circuit(46.0, 63.0, 15.0, 75.0, 20.0), 2: _circuit(29.0, 37.0, 5.0, 50.0, 26.0)},
    hot_water={1: HotWaterParameters(setpoint=55.0, recharge_below=45.0)},
    buffers={1: BufferParameters(circuit_release=30.0, ignition_below_buffer_temperature=55.0)},
    raw={40001: 140, 48047: 1, 48048: 1},
)

FIXTURES = Path(__file__).parent / "fixtures" / "cloud"


def load_fixture(name: str) -> Any:
    """Parsed JSON of a cloud fixture."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


CLOUD_COMPONENTS = parse_component_list(load_fixture("component_list.json"))
CLOUD_OVERVIEW = parse_overview(load_fixture("overview.json"))
CLOUD_DETAILS = tuple(
    parse_component_detail(load_fixture(f"component_{c.component_id}.json")) for c in CLOUD_COMPONENTS
)
CLOUD_SCHEDULES = {d.component.key: d.schedule for d in CLOUD_DETAILS if d.schedule}
SYSTEM_INFO = parse_system_info(load_fixture("info.json"))
HEADER = parse_header(load_fixture("header.json"))
CLOUD_ERRORS = parse_errors(load_fixture("errors.json"))
CLOUD_STATE = CloudBoilerState(operating_mode=OperatingMode.AUTOMATIC, remote_control_allowed=True)


CLOUD_DETAILS_BY_ID = {detail.component.component_id: detail for detail in CLOUD_DETAILS}


def cloud_components(unknown: Iterable[int]) -> list[CloudComponent]:
    """The component list with heating circuits of another sub type than 'OUT_TEMP_CRTL'.

    The list carries no picture URL: for those circuits the room sensor is unknown (None) and
    only the component detail tells.
    """
    unknown = set(unknown)
    return [
        replace(c, sub_type=None, room_sensor=None) if c.key == f"hc{c.number}" and c.number in unknown else c
        for c in CLOUD_COMPONENTS
    ]


def cloud_overview(unknown: Iterable[int]) -> CloudOverview:
    """The overview with heating circuits of another sub type than 'OUT_TEMP_CRTL' (room sensor None)."""
    unknown = set(unknown)
    return replace(
        CLOUD_OVERVIEW,
        components=tuple(
            replace(item, component=replace(item.component, sub_type=None, room_sensor=None))
            if item.component.key == f"hc{item.component.number}" and item.component.number in unknown
            else item
            for item in CLOUD_OVERVIEW.components
        ),
    )


EQUIPMENT_DATA = {
    CONF_HEATING_CIRCUITS: [1, 2],
    CONF_HOT_WATER: [1],
    CONF_BUFFERS: [1],
    CONF_CIRCULATION_PUMP: True,
}


def entry_data(
    connection: ConnectionType = ConnectionType.HYBRID, room_sensors: Iterable[int] = ()
) -> dict[str, Any]:
    """Data of a configured entry; the fixtures report no room sensor."""
    data: dict[str, Any] = {CONF_EQUIPMENT: dict(EQUIPMENT_DATA), CONF_ROOM_SENSORS: list(room_sensors)}
    if connection is not ConnectionType.CLOUD:
        data.update({CONF_HOST: HOST, CONF_PORT: 502, CONF_SLAVE: 2})
    if connection is not ConnectionType.MODBUS:
        data[CONF_CLOUD] = {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD, CONF_FACILITY_ID: FACILITY_ID}
    return data


ENTRY_OPTIONS = {CONF_SCAN_INTERVAL: 30, CONF_PARAMETER_INTERVAL: 5, CONF_CLOUD_INTERVAL: 2}
