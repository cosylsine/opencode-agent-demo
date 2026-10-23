"""Versioned, illustrative mass/energy balance for the offline steam cycle B.

This is deliberately a reduced dynamic model, not a commissioned plant twin.
Runs share nominal initial inventories; component wear changes their dynamics.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from hashlib import sha256
from math import isfinite

from packages.contracts.tuning import PlantState

VERSION = "steam-cycle-b-v2"
SCENARIO_VERSION = "load-suite-v1"
NOMINAL_FLOW = 3.26 * 1000 / 3600  # kg/s
NOMINAL_POWER = 500.0  # kW
NOMINAL_PRESSURE = 3.8  # MPa
NOMINAL_HEAT = 2500.0  # kW
BASE_PID = {"kp": 0.2, "ki": 0.005, "kd": 0.0}
PID_BOUNDS = {"kp": (0.0, 4.0), "ki": (0.0, 0.25), "kd": (0.0, 8.0)}
DT = 1.0
DURATION = 420


@dataclass(frozen=True)
class Event:
    at_s: int
    until_s: int | None
    target_kw: float
    kind: str


SCENARIOS: dict[str, tuple[str, tuple[Event, ...]]] = {
    "step_up": ("负荷上升阶跃", (Event(60, None, 550.0, "step"),)),
    "step_down": ("负荷下降阶跃", (Event(60, None, 450.0, "step"),)),
    "ramp_up": ("负荷上升斜坡", (Event(60, 180, 600.0, "ramp"),)),
    "repeated": ("负荷多次升降", (
        Event(60, None, 575.0, "step"),
        Event(180, None, 425.0, "step"),
        Event(300, None, 525.0, "step"),
    )),
}

ASSUMPTIONS = {
    "model": VERSION,
    "scenario_version": SCENARIO_VERSION,
    "description": "简化仿真模型；参数未经现场校准，不可直接用于回路 A",
    "nominal_power_kw": NOMINAL_POWER,
    "nominal_steam_flow_kg_s": NOMINAL_FLOW,
    "nominal_heat_kw": NOMINAL_HEAT,
    "nominal_pressure_mpa": NOMINAL_PRESSURE,
    "condenser_pressure_mpa": 0.019,
    "feedwater_temperature_c": 60.0,
    "sample_period_s": DT,
    "duration_s": DURATION,
    "steam_specific_work_kj_kg": NOMINAL_POWER / NOMINAL_FLOW,
    "evaporation_specific_heat_kj_kg": NOMINAL_HEAT / NOMINAL_FLOW,
    "drum_nominal_water_kg": 1250.0,
    "drum_nominal_steam_kg": 80.0,
    "condenser_nominal_water_kg": 1500.0,
    "pump_speed_nominal_fraction": 0.7,
    "pump_speed_limits_fraction": [0.35, 1.0],
    "pump_flow_lag_s": 6.0,
    "turbine_flow_lag_s": 8.0,
    "boiler_heat_lag_s": 45.0,
    "pressure_inventory_coefficient_mpa_per_kg": 0.004,
    "level_limits_fraction": [0.3, 0.7],
    "pressure_limits_mpa": [3.42, 4.18],
    "flow_limits_kg_s": [0.0, 1.45],
    "valve_policy": "固定负荷前馈开度：0.7 × 目标功率 / 500 kW，限制在 0.5～0.9",
    "fuel_policy": "固定压力调节律，热输入限制在 1.5～3.2 MW",
    "pump_pid_form": "并联式；误差=(给水目标-实测流量)/额定流量；输出=泵速额定占比；微分作用于测量值",
    "aging_scale": "0～1 为模型老化强度，0 为标称；不对应实际服役年限或失效概率",
    "aging_effects": {
        "feedwater_pump": "扬程系数最多下降 12%；转速与流量响应时间最多增至 3 倍",
        "feedwater_valve": "通流能力最多下降 18%；开度响应时间由 1 s 增至 12 s",
        "boiler": "有效热效率最多下降 12%；热响应时间最多增至 2 倍",
        "turbine": "比功最多下降 10%；流量响应时间最多增至 2.5 倍",
        "condenser": "背压由 0.019 MPa 增至 0.057 MPa，折算循环比功最多下降 6%",
    },
    "initial_state_policy": "相同标称水汽库存、泵速、流量和热输入；有老化时初始点不保证平衡，前 60 s 的漂移也参与评分",
    "aging_policy": "每次调优固定老化状态；基线与全部候选使用同一状态和安全边界；严重老化可能无法仅靠 PID 消除越界",
}


def plant_state_hash(state: PlantState) -> str:
    """Physical identity excludes the display name and includes model semantics."""
    data = {"model_version": VERSION, "aging": state.aging.model_dump()}
    return sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def model_parameters(state: PlantState) -> dict[str, float]:
    aging = state.aging
    return {
        "pump_head_factor": 1.0 - 0.12 * aging.feedwater_pump,
        "pump_speed_lag_s": 4.0 * (1.0 + 2.0 * aging.feedwater_pump),
        "pump_flow_lag_s": 6.0 * (1.0 + 2.0 * aging.feedwater_pump),
        "valve_capacity_factor": 1.0 - 0.18 * aging.feedwater_valve,
        "valve_lag_s": 1.0 + 11.0 * aging.feedwater_valve,
        "boiler_efficiency_factor": 1.0 - 0.12 * aging.boiler,
        "boiler_heat_lag_s": 45.0 * (1.0 + aging.boiler),
        "turbine_work_factor": 1.0 - 0.10 * aging.turbine,
        "turbine_flow_lag_s": 8.0 * (1.0 + 1.5 * aging.turbine),
        "condenser_pressure_mpa": 0.019 * (1.0 + 2.0 * aging.condenser),
        "condenser_work_factor": 1.0 - 0.06 * aging.condenser,
    }


def scenario_events() -> dict[str, list[dict]]:
    return {key: [asdict(event) for event in events] for key, (_, events) in SCENARIOS.items()}


def demand_at(events: tuple[Event, ...], t: int) -> float:
    demand = NOMINAL_POWER
    for event in events:
        if t < event.at_s:
            break
        if event.kind == "ramp" and event.until_s and t < event.until_s:
            return demand + (event.target_kw - demand) * (t - event.at_s) / (event.until_s - event.at_s)
        demand = event.target_kw
    return demand


def clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def simulate(
    pid: dict[str, float], scenario_id: str, plant_state: PlantState | None = None,
) -> list[dict[str, float]]:
    if set(pid) != set(PID_BOUNDS) or any(
        not isfinite(value) or not PID_BOUNDS[key][0] <= value <= PID_BOUNDS[key][1]
        for key, value in pid.items()
    ):
        raise ValueError("PID 参数超出仿真绝对边界")
    if scenario_id not in SCENARIOS:
        raise ValueError("未知负荷工况")
    params = model_parameters(plant_state or PlantState())
    work_factor = params["turbine_work_factor"] * params["condenser_work_factor"]
    events = SCENARIOS[scenario_id][1]
    water, steam, condensate = 1250.0, 80.0, 1500.0
    pump_speed, feed_flow, turbine_flow = 0.7, NOMINAL_FLOW, NOMINAL_FLOW
    heat_kw, pressure = NOMINAL_HEAT, NOMINAL_PRESSURE
    integral, last_measurement = 0.0, NOMINAL_FLOW
    valve_position = 0.7
    rows: list[dict[str, float]] = []
    for t in range(DURATION + 1):
        target_kw = demand_at(events, t)
        level = 0.5 + (water - 1250.0) / 1250.0
        # Load feedforward with a slower inventory correction. Pump PID controls flow.
        feed_target = clamp(turbine_flow + 0.006 * (0.5 - level) * 100, 0.0, 1.45)
        e = (feed_target - feed_flow) / NOMINAL_FLOW
        derivative = -(feed_flow - last_measurement) / NOMINAL_FLOW / DT
        raw = 0.7 + pid["kp"] * e + pid["ki"] * integral + pid["kd"] * derivative
        commanded = clamp(raw, 0.35, 1.0)
        # Conditional integration prevents windup at pump speed limits.
        if (raw == commanded) or (raw > 1.0 and e < 0) or (raw < 0.35 and e > 0):
            integral = clamp(integral + e * DT, -20.0, 20.0)
        last_measurement = feed_flow
        valve_command = clamp(0.7 * target_kw / NOMINAL_POWER, 0.5, 0.9)
        valve_position += (valve_command - valve_position) * DT / params["valve_lag_s"]
        actual_kw = turbine_flow * (NOMINAL_POWER / NOMINAL_FLOW) * (pressure / NOMINAL_PRESSURE) ** 0.5 * work_factor
        rows.append({
            "time_s": float(t), "target_power_kw": target_kw, "power_kw": actual_kw,
            "feed_target_kg_s": feed_target, "feed_flow_kg_s": feed_flow,
            "pump_speed_fraction": pump_speed, "valve_open_fraction": valve_position,
            "drum_level_fraction": level, "steam_pressure_mpa": pressure,
            "drum_water_kg": water, "drum_steam_kg": steam,
            "condenser_water_kg": condensate, "heat_input_kw": heat_kw,
            "steam_flow_kg_s": turbine_flow,
        })
        if t == DURATION:
            break
        # The turbine governor, firing controller, valve and pump policy are fixed.
        turbine_target = NOMINAL_FLOW * target_kw / NOMINAL_POWER * (NOMINAL_PRESSURE / pressure) ** 0.5 / work_factor
        turbine_flow += (turbine_target - turbine_flow) * DT / params["turbine_flow_lag_s"]
        firing_target = clamp(NOMINAL_HEAT * turbine_flow / NOMINAL_FLOW / params["boiler_efficiency_factor"] + 300.0 * (NOMINAL_PRESSURE - pressure), 1500.0, 3200.0)
        heat_kw += (firing_target - heat_kw) * DT / params["boiler_heat_lag_s"]
        pump_speed += (commanded - pump_speed) * DT / params["pump_speed_lag_s"]
        # A rated pump head above drum pressure and fixed valve characteristic.
        hydraulic = max(0.0, (pump_speed / 0.7) ** 2 * 4.8 * params["pump_head_factor"] - pressure)
        rated_head = 4.8 - NOMINAL_PRESSURE
        flow_equilibrium = NOMINAL_FLOW * (valve_position / 0.7) * (hydraulic / rated_head) ** 0.5 * params["valve_capacity_factor"]
        feed_flow += (flow_equilibrium - feed_flow) * DT / params["pump_flow_lag_s"]
        evaporation = max(0.0, heat_kw * params["boiler_efficiency_factor"] / (NOMINAL_HEAT / NOMINAL_FLOW))
        # Explicit mass conservation across drum steam/water and condenser.
        water += (feed_flow - evaporation) * DT
        steam += (evaporation - turbine_flow) * DT
        condensate += (turbine_flow - feed_flow) * DT
        # Pressure follows steam inventory and heat content around the nominal point.
        pressure = max(0.1, NOMINAL_PRESSURE + 0.004 * (steam - 80.0))
    return rows
