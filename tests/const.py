"""Test data: values decoded from the real register snapshot of 24.10.2026."""

from froeling_lambdatronic import (
    BufferValues,
    CloudBoilerState,
    Equipment,
    HeatingCircuitParameters,
    HeatingCircuitValues,
    HotWaterParameters,
    HotWaterValues,
    LambdatronicParameters,
    LambdatronicValues,
    OperatingMode,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_PORT, CONF_USERNAME

from custom_components.froeling_lambdatronic.const import (
    CONF_BUFFERS,
    CONF_CLOUD,
    CONF_EQUIPMENT,
    CONF_FACILITY_ID,
    CONF_HEATING_CIRCUITS,
    CONF_HOT_WATER,
    CONF_PARAMETER_INTERVAL,
    CONF_SCAN_INTERVAL,
    CONF_SLAVE,
)

ENTRY_ID = "01K8FROELING0TEST0ENTRY000"
HOST = "192.168.70.18"
UNIQUE_ID = f"{HOST}:502:2"
FACILITY_ID = 4711
USERNAME = "kessel@example.com"
PASSWORD = "geheim"

EQUIPMENT = Equipment(heating_circuits=(1, 2), hot_water=(1,), buffers=(1,))

VALUES = LambdatronicValues(
    boiler_temperature=63.0,
    flue_gas_temperature=61.0,
    residual_oxygen=21.0,
    induced_draft_speed=0.0,
    return_temperature=0.0,
    boiler_output=32.0,
    operating_hours=17697.0,
    pellet_hopper_level=100.0,
    burner_starts=10473.0,
    feed_rate=0.0,
    boiler_setpoint_calculated=70.0,
    heating_hours=12835.0,
    pellet_counter_kg=94.0,
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
    raw={30001: 126, 34001: 2, 34002: 19},
)

PARAMETERS = LambdatronicParameters(
    boiler_setpoint=70.0,
    pellet_stock=4.5,
    pellet_stock_minimum=0.0,
    heating_circuits={
        1: HeatingCircuitParameters(
            curve_flow_plus10=46.0,
            curve_flow_minus10=63.0,
            setback=15.0,
            max_flow=75.0,
            heating_limit=20.0,
            heating_limit_setback=7.0,
            operating_mode=1,
        ),
        2: HeatingCircuitParameters(
            curve_flow_plus10=29.0,
            curve_flow_minus10=37.0,
            setback=5.0,
            max_flow=50.0,
            heating_limit=26.0,
            heating_limit_setback=7.0,
            operating_mode=1,
        ),
    },
    hot_water={1: HotWaterParameters(setpoint=55.0, recharge_below=45.0)},
    raw={40001: 140, 48047: 1, 48048: 1},
)

CLOUD_STATE = CloudBoilerState(operating_mode=OperatingMode.AUTOMATIC, remote_control_allowed=True)


def entry_data(*, cloud: bool = True) -> dict[str, object]:
    """Data of a configured entry."""
    data: dict[str, object] = {
        CONF_HOST: HOST,
        CONF_PORT: 502,
        CONF_SLAVE: 2,
        CONF_EQUIPMENT: {CONF_HEATING_CIRCUITS: [1, 2], CONF_HOT_WATER: [1], CONF_BUFFERS: [1]},
    }
    if cloud:
        data[CONF_CLOUD] = {CONF_USERNAME: USERNAME, CONF_PASSWORD: PASSWORD, CONF_FACILITY_ID: FACILITY_ID}
    return data


ENTRY_OPTIONS = {
    CONF_HEATING_CIRCUITS: [1, 2],
    CONF_HOT_WATER: [1],
    CONF_BUFFERS: [1],
    CONF_SCAN_INTERVAL: 30,
    CONF_PARAMETER_INTERVAL: 5,
}
