"""Small, deterministic boundary for monitoring values returned by a control system.

An integration owns the read protocol. It must return one coherent frame: values,
parameter version and effective time must describe the same operating interval.
"""

from collections.abc import Awaitable, Callable, Mapping
from datetime import datetime, timezone
from typing import Any, Literal, Protocol

from pydantic import Field, model_validator

from packages.contracts.common import PID, Model, Number, SourceKind, Time


class Signal(Model):
    value: Number | None
    unit: str
    quality: Literal["GOOD", "MISSING", "BAD"] = "GOOD"

    @model_validator(mode="after")
    def valid_quality(self):
        if self.quality == "GOOD" and self.value is None:
            raise ValueError("GOOD requires a value")
        if self.quality == "MISSING" and self.value is not None:
            raise ValueError("MISSING cannot contain a value")
        return self


class TelemetryFrame(Model):
    observed_at: Time
    control_loop_id: str
    source_kind: SourceKind
    parameter_version: str | None = None
    parameters: PID | None = None
    signals: dict[str, Signal]

    @model_validator(mode="after")
    def parameter_consistency(self):
        if (self.parameters is None) != (self.parameter_version is None):
            raise ValueError("Parameters and their version must be supplied together")
        return self


class SignalRule(Model):
    unit: str
    minimum: Number | None = None
    maximum: Number | None = None
    max_rate_per_second: Number | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def valid_interval(self):
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum exceeds maximum")
        return self


class MonitorRules(Model):
    signals: dict[str, SignalRule]
    max_age_seconds: Number = Field(gt=0)

    @model_validator(mode="after")
    def nonempty(self):
        if not self.signals:
            raise ValueError("At least one signal rule is required")
        return self


class SignalIssue(Model):
    signal: str
    code: Literal["MISSING", "BAD_QUALITY", "UNIT_MISMATCH", "BELOW_MIN", "ABOVE_MAX", "RATE_EXCEEDED", "STALE", "TIME_REVERSED"]


class MonitorResult(Model):
    state: Literal["NORMAL", "ABNORMAL", "UNKNOWN"]
    observed_at: Time
    source_kind: SourceKind
    parameter_version: str | None
    issues: list[SignalIssue]


class TelemetryReader(Protocol):
    async def read_frame(self, control_loop_id: str) -> TelemetryFrame: ...


class PayloadMapping(Model):
    """Dotted paths into one atomic response from a vendor client."""

    observed_at: str
    control_loop_id: str
    parameter_version: str | None = None
    parameters: dict[str, str] = Field(default_factory=dict)
    signals: dict[str, str]
    signal_units: dict[str, str]
    signal_quality: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def complete(self):
        if not self.signals or set(self.signals) != set(self.signal_units):
            raise ValueError("Signal mappings must be complete")
        if bool(self.parameters) != bool(self.parameter_version):
            raise ValueError("Parameter mapping requires a version path")
        if not set(self.signal_quality).issubset(self.signals):
            raise ValueError("Quality mappings must name configured signals")
        return self


def _path(payload: Mapping[str, Any], dotted: str) -> Any:
    value: Any = payload
    for key in dotted.split("."):
        if not isinstance(value, Mapping) or key not in value:
            raise ValueError(f"Missing mapped field: {dotted}")
        value = value[key]
    return value


class MappedTelemetryReader:
    """Adapt one local vendor response; fetch must provide a coherent read."""

    def __init__(self, fetch: Callable[[str], Awaitable[Mapping[str, Any]]],
                 mapping: PayloadMapping, source_kind: SourceKind = SourceKind.REAL):
        self.fetch = fetch
        self.mapping = mapping
        self.source_kind = source_kind

    async def read_frame(self, control_loop_id: str) -> TelemetryFrame:
        raw = await self.fetch(control_loop_id)
        mapped_loop = _path(raw, self.mapping.control_loop_id)
        if mapped_loop != control_loop_id:
            raise ValueError("Control loop mismatch")
        values = {name: _path(raw, path)
                  for name, path in self.mapping.parameters.items()}
        signals = {}
        for name, path in self.mapping.signals.items():
            value = _path(raw, path)
            quality_path = self.mapping.signal_quality.get(name)
            quality = _path(raw, quality_path) if quality_path else (
                "MISSING" if value is None else "GOOD"
            )
            signals[name] = Signal(value=value, unit=self.mapping.signal_units[name],
                                   quality=quality)
        return TelemetryFrame(
            observed_at=_path(raw, self.mapping.observed_at),
            control_loop_id=mapped_loop,
            source_kind=self.source_kind,
            parameter_version=(
                _path(raw, self.mapping.parameter_version)
                if self.mapping.parameter_version else None
            ),
            parameters=PID.model_validate(values) if values else None,
            signals=signals,
        )


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class RuleMonitor:
    """Constant-time checks per configured signal, with no model call in the alarm path."""

    def __init__(self, rules: MonitorRules):
        self.rules = rules

    def check(
        self,
        frame: TelemetryFrame,
        previous: TelemetryFrame | None = None,
        checked_at: datetime | None = None,
    ) -> MonitorResult:
        checked_at = checked_at or datetime.now(timezone.utc)
        if checked_at.tzinfo is None:
            raise ValueError("checked_at must have a timezone")
        issues: list[SignalIssue] = []
        age = (checked_at - _time(frame.observed_at)).total_seconds()
        if age < 0 or age > self.rules.max_age_seconds:
            issues.append(SignalIssue(signal="*", code="STALE"))
        elapsed = None
        if previous is not None:
            if (previous.control_loop_id != frame.control_loop_id
                    or previous.source_kind != frame.source_kind
                    or previous.parameter_version != frame.parameter_version):
                previous = None  # Never compute a rate across a different regime.
            else:
                elapsed = (_time(frame.observed_at) - _time(previous.observed_at)).total_seconds()
                if elapsed <= 0:
                    issues.append(SignalIssue(signal="*", code="TIME_REVERSED"))
                    previous = None
        for name, rule in self.rules.signals.items():
            signal = frame.signals.get(name)
            if signal is None or signal.quality == "MISSING":
                issues.append(SignalIssue(signal=name, code="MISSING"))
                continue
            if signal.quality == "BAD":
                issues.append(SignalIssue(signal=name, code="BAD_QUALITY"))
                continue
            if signal.unit != rule.unit:
                issues.append(SignalIssue(signal=name, code="UNIT_MISMATCH"))
                continue
            if rule.minimum is not None and signal.value < rule.minimum:
                issues.append(SignalIssue(signal=name, code="BELOW_MIN"))
            if rule.maximum is not None and signal.value > rule.maximum:
                issues.append(SignalIssue(signal=name, code="ABOVE_MAX"))
            prior = previous.signals.get(name) if previous else None
            if (prior and prior.quality == "GOOD" and prior.unit == signal.unit
                    and rule.max_rate_per_second is not None and elapsed is not None
                    and abs(signal.value - prior.value) / elapsed > rule.max_rate_per_second):
                issues.append(SignalIssue(signal=name, code="RATE_EXCEEDED"))
        unknown = {"MISSING", "BAD_QUALITY", "UNIT_MISMATCH", "STALE", "TIME_REVERSED"}
        state = ("UNKNOWN" if any(issue.code in unknown for issue in issues)
                 else "ABNORMAL" if issues else "NORMAL")
        return MonitorResult(
            state=state,
            observed_at=frame.observed_at,
            source_kind=frame.source_kind,
            parameter_version=frame.parameter_version,
            issues=issues,
        )
