"""Safety first, then comparable multi-scenario simulation scores."""

from __future__ import annotations

from statistics import mean

from packages.contracts.tuning import PlantState

from .plant import (
    ASSUMPTIONS,
    NOMINAL_FLOW,
    NOMINAL_POWER,
    NOMINAL_PRESSURE,
    SCENARIOS,
    plant_state_hash,
    simulate,
)

SCORE_VERSION = "steam-score-v1"
WEIGHTS = {"flow": 0.4, "level": 0.2, "pressure": 0.2, "power": 0.1, "pump_motion": 0.1}


def evaluate_trace(rows: list[dict]) -> dict:
    if not rows or any(not all(isinstance(value, (float, int)) for value in row.values()) for row in rows):
        raise ValueError("仿真输出无效")
    limits = ASSUMPTIONS
    violations = []
    for row in rows:
        for name, field, bounds in (
            ("DRUM_LEVEL", "drum_level_fraction", limits["level_limits_fraction"]),
            ("STEAM_PRESSURE", "steam_pressure_mpa", limits["pressure_limits_mpa"]),
            ("FEED_FLOW", "feed_flow_kg_s", limits["flow_limits_kg_s"]),
            ("PUMP_SPEED", "pump_speed_fraction", limits["pump_speed_limits_fraction"]),
        ):
            if not bounds[0] - 1e-9 <= row[field] <= bounds[1] + 1e-9 and name not in violations:
                violations.append(name)
    values = {
        "flow": mean(abs(r["feed_flow_kg_s"] - r["feed_target_kg_s"]) / NOMINAL_FLOW for r in rows),
        "level": mean(abs(r["drum_level_fraction"] - 0.5) / 0.2 for r in rows),
        "pressure": mean(abs(r["steam_pressure_mpa"] - NOMINAL_PRESSURE) / (0.1 * NOMINAL_PRESSURE) for r in rows),
        "power": mean(abs(r["power_kw"] - r["target_power_kw"]) / NOMINAL_POWER for r in rows),
        "pump_motion": sum(abs(b["pump_speed_fraction"] - a["pump_speed_fraction"]) for a, b in zip(rows, rows[1:])) / len(rows),
    }
    return {
        "safe": not violations,
        "violations": violations,
        "score": sum(WEIGHTS[key] * values[key] for key in WEIGHTS),
        "components": values,
        "max_level_error": max(abs(r["drum_level_fraction"] - 0.5) for r in rows),
        "max_pressure_error_mpa": max(abs(r["steam_pressure_mpa"] - NOMINAL_PRESSURE) for r in rows),
        "mass_balance_max_error_kg": max(abs(r["drum_water_kg"] + r["drum_steam_kg"] + r["condenser_water_kg"] - 2830.0) for r in rows),
    }


def evaluate_pid(
    pid: dict[str, float], plant_state: PlantState | None = None,
) -> tuple[dict, dict[str, list[dict]]]:
    state = plant_state or PlantState()
    traces = {scenario: simulate(pid, scenario, state) for scenario in SCENARIOS}
    per_scenario = {scenario: evaluate_trace(rows) for scenario, rows in traces.items()}
    scores = [entry["score"] for entry in per_scenario.values()]
    result = {
        "score_version": SCORE_VERSION,
        "plant_state_hash": plant_state_hash(state),
        "safe": all(entry["safe"] for entry in per_scenario.values()),
        "score": 0.7 * mean(scores) + 0.3 * max(scores),
        "worst_scenario": max(per_scenario, key=lambda key: per_scenario[key]["score"]),
        "scenarios": per_scenario,
    }
    return result, traces
