"""Versioned deterministic rules. No model output can change these defaults."""

import hashlib
import json
import math
from datetime import datetime, timedelta
from decimal import Decimal

import numpy as np

from packages.contracts.agents import ControlOutput, RuleAssessment
from packages.contracts.common import PID, Constraints, MetricsConfig
from packages.contracts.observations import Metrics, QualityReport, SeriesPoint

CONFIG = MetricsConfig(
    metrics_version="metrics-v1",
    steady_tail_fraction=0.2,
    settling_band_absolute=0.1,
    settling_hold_seconds=10.0,
    hard_pressure_min=29.0,
    hard_pressure_max=30.8,
    target_error=0.08,
    target_overshoot=0.02,
    target_std=0.02,
    target_settling=40.0,
    min_improvement=0.001,
)
CONSTRAINTS = Constraints.model_validate_json(
    json.dumps(
        {
            "kp": {"min": 0.0, "max": 3.0, "max_delta": 0.1},
            "ki": {"min": 0.0, "max": 1.0, "max_delta": 0.05},
            "kd": {"min": 0.0, "max": 0.5, "max_delta": 0.02},
            "equality_tolerance": 0.000001,
        }
    )
)
INITIAL = PID(kp=1.0, ki=0.2, kd=0.05)
COUPLED_INITIAL = PID(kp=1.0, ki=0.15, kd=0.03)
REFERENCE = PID(kp=1.1, ki=0.2, kd=0.05)
SCENARIOS = {
    "normal": "正常稳定",
    "improvable": "可改善的稳态偏高",
    "no_improvement": "固定曲线，无改善",
    "coupled_load": "耦合负载，多参数调优",
}
DESCRIPTION = (
    "仅用于 EXAMPLE 的泵出口压力虚拟回路，目标 30 demo_unit，离线试验 120 秒。"
    "三个参数共同影响稳态偏差、衰减振荡和负载扰动；参考点 kp=1.1、ki=0.2、kd=0.05。"
    "稳态损失 q=1+((kp-1.1)/0.1)^2+((ki-0.2)/0.05)^2+((kd-0.05)/0.02)^2；"
    "试验还叠加与参数有关的衰减振荡、积分偏置及周期负载扰动。"
    "coupled_load 工况起始 kp=1.0、ki=0.15、kd=0.03，负载扰动更强，"
    "通常需要多轮比较不同参数方向；no_improvement 工况与候选参数无关。"
    "只相对 current_best 小步提出一个候选。"
)


class DomainError(Exception):
    def __init__(self, code: str, message: str = "", status: int = 409):
        super().__init__(message or code)
        self.code, self.message, self.status = code, message or code, status


def canonical(value) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if value is None or isinstance(value, (bool, str)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise ValueError("non-finite number")
        number = Decimal(str(value))
        return "0" if number == 0 else format(number.normalize(), "f")
    if isinstance(value, list):
        return "[" + ",".join(canonical(v) for v in value) + "]"
    if isinstance(value, dict) and all(isinstance(k, str) for k in value):
        return "{" + ",".join(canonical(k) + ":" + canonical(value[k]) for k in sorted(value)) + "}"
    raise TypeError("unsupported canonical type")


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def context_hash(context) -> str:
    return digest(context.model_dump(mode="json", exclude={"context_hash", "context_id"}))


def request_hash(request) -> str:
    return digest(request.model_dump(mode="json", exclude={"request_hash", "trace_id"}))


def curve(pid: PID, scenario: str, start: str, duration: int = 120) -> list[SeriesPoint]:
    if scenario not in SCENARIOS:
        raise DomainError("SCENARIO_NOT_FOUND", status=404)
    origin = datetime.fromisoformat(start.replace("Z", "+00:00"))
    return [
        SeriesPoint(
            timestamp=(origin + timedelta(seconds=t)).isoformat().replace("+00:00", "Z"),
            elapsed_seconds=float(t),
            pressure=example_pressure(pid, scenario, float(t)),
            target_pressure=30.0,
            quality="GOOD",
        )
        for t in range(duration + 1)
    ]


def example_pressure(pid: PID, scenario: str, elapsed: float) -> float:
    """Deterministic virtual plant used by both live sampling and offline trials."""
    if scenario not in SCENARIOS:
        raise DomainError("SCENARIO_NOT_FOUND", status=404)
    if scenario == "no_improvement":
        q, kp_error, ki_error, kd_error = 2.0, 0.0, 0.0, 0.0
        effective_kd = REFERENCE.kd
    else:
        kp_error = (pid.kp - REFERENCE.kp) / 0.1
        ki_error = (pid.ki - REFERENCE.ki) / 0.05
        kd_error = (pid.kd - REFERENCE.kd) / 0.02
        q = 1 + kp_error**2 + ki_error**2 + kd_error**2
        effective_kd = pid.kd
    coupled = scenario == "coupled_load"
    transient = q * (0.05 + 0.15 * math.exp(-elapsed / 10))
    ring = (
        (0.012 if coupled else 0.006)
        * (1 + abs(kp_error) + 0.5 * abs(ki_error))
        / (1 + max(effective_kd, 0) * 4)
        * math.exp(-elapsed / 25)
        * math.sin(elapsed / 3)
    )
    integral_bias = (0.003 if coupled else 0.002) * ki_error * (1 - math.exp(-elapsed / 40))
    load = (0.012 if coupled else 0.0025) * math.sin(elapsed / 11) * (
        1 - math.exp(-elapsed / 15)
    )
    return 30 + transient + ring + integral_bias + load


def quality(
    points: list[SeriesPoint], start: str | None = None, duration=120.0, period=1.0, target=30.0
) -> QualityReport:
    reasons = []
    missing = sum(p.pressure is None or p.quality == "MISSING" for p in points)
    bad = sum(p.quality == "BAD" for p in points)
    if len(points) != int(duration / period) + 1:
        reasons.append("SAMPLE_COUNT")
    if missing or bad:
        reasons.append("SAMPLE_QUALITY")
    if points:
        origin = datetime.fromisoformat((start or points[0].timestamp).replace("Z", "+00:00"))
        for i, point in enumerate(points):
            actual = (
                datetime.fromisoformat(point.timestamp.replace("Z", "+00:00")) - origin
            ).total_seconds()
            if (
                abs(point.elapsed_seconds - i * period) > 1e-6
                or abs(actual - point.elapsed_seconds) > 1e-6
            ):
                reasons.append("TIMESTAMP_MISMATCH")
                break
        if any(p.target_pressure != target for p in points):
            reasons.append("TARGET_MISMATCH")
        valid_pressures = [p.pressure for p in points if p.pressure is not None]
        if len(valid_pressures) > 1 and max(valid_pressures) == min(valid_pressures):
            reasons.append("FROZEN_SIGNAL")
    return QualityReport(
        valid=not reasons,
        sample_count=len(points),
        missing_count=missing,
        bad_count=bad,
        reason_codes=reasons,
    )


def metrics(points: list[SeriesPoint], config: MetricsConfig = CONFIG) -> Metrics:
    if not quality(points).valid:
        raise DomainError("DATA_INVALID")
    pressure = np.array([p.pressure for p in points], dtype=float)
    times = np.array([p.elapsed_seconds for p in points])
    target = points[0].target_pressure
    tail = pressure[-math.ceil(len(points) * config.steady_tail_fraction) :]
    signed = float(np.mean(tail) - target)
    within = np.abs(pressure - target) <= config.settling_band_absolute
    settling = next(
        (
            float(times[i])
            for i in range(len(points))
            if times[-1] - times[i] >= config.settling_hold_seconds and within[i:].all()
        ),
        None,
    )
    return Metrics(
        metrics_version=config.metrics_version,
        signed_steady_error=signed,
        steady_state_error=abs(signed),
        iae=float(np.trapezoid(np.abs(pressure - target), times)),
        overshoot_ratio=max(0.0, float((pressure.max() - target) / max(abs(target), 1e-9))),
        pressure_std=float(np.std(tail)),
        settling_time_s=settling,
        unavailable_reasons=[] if settling is not None else ["NOT_SETTLED"],
    )


def score(m: Metrics) -> float:
    return (
        0.4 * m.steady_state_error
        + 0.2 * m.overshoot_ratio / 0.1
        + 0.2 * m.pressure_std
        + 0.2 * (120.0 if m.settling_time_s is None else m.settling_time_s) / 120
    )


def reached(m: Metrics, c: MetricsConfig = CONFIG) -> bool:
    return (
        m.steady_state_error <= c.target_error
        and m.overshoot_ratio <= c.target_overshoot
        and m.pressure_std <= c.target_std
        and m.settling_time_s is not None
        and m.settling_time_s <= c.target_settling
    )


def safe(points: list[SeriesPoint], c: MetricsConfig = CONFIG) -> bool:
    return all(
        p.pressure is not None and c.hard_pressure_min <= p.pressure <= c.hard_pressure_max
        for p in points
    )


def assess(m: Metrics) -> RuleAssessment:
    state = (
        "STEADY_HIGH"
        if m.signed_steady_error > CONFIG.target_error
        else "STEADY_LOW"
        if m.signed_steady_error < -CONFIG.target_error
        else "NORMAL_STABLE"
        if reached(m)
        else "UNCERTAIN"
    )
    return RuleAssessment(assessment=state, reason_codes=[state])


def guard(output: ControlOutput, best: PID, seen: list[PID], c=CONSTRAINTS) -> PID:
    if output.decision == "ABSTAIN":
        raise DomainError("CONTROL_ABSTAIN")
    bounds = c.bounds()
    if (
        set(output.candidate_pid.root) != set(best.root)
        or set(output.delta.root) != set(best.root)
        or set(bounds) != set(best.root)
    ):
        raise DomainError("PID_PARAMETER_MISMATCH")
    for key in best.root:
        value, delta, old = (
            output.candidate_pid.root[key],
            output.delta.root[key],
            best.root[key],
        )
        bound = bounds[key]
        # Decimal input precision is a contract check, not floating point tolerance.
        for x in (value, delta):
            if Decimal(str(x)) != Decimal(str(x)).quantize(Decimal("0.000001")):
                raise DomainError("PID_PRECISION")
        actual_step = abs(Decimal(str(value)) - Decimal(str(old)))
        if (
            not bound.min <= value <= bound.max
            or abs(delta) > bound.max_delta
            or actual_step > Decimal(str(bound.max_delta))
        ):
            raise DomainError("PID_BOUNDS")
        if abs(value - old - delta) > c.equality_tolerance:
            raise DomainError("PID_DELTA_MISMATCH")
    if any(digest(output.candidate_pid) == digest(p) for p in seen):
        raise DomainError("DUPLICATE_CANDIDATE")
    return output.candidate_pid
