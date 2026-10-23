from typing import Annotated, Literal

from pydantic import Field, model_validator

from .common import ID, PID, Envelope, Model, Number, Reasons, SourceKind, Time


class SnapshotQuery(Envelope):
    control_loop_id: str
    as_of: Time
    window_seconds: Annotated[int, Field(gt=0, le=120)]
    request_id: ID
    scenario_id: str | None


class PidSnapshot(Model):
    pid: PID
    pid_version: str
    effective_at: Time
    pid_form: str
    parameter_units: dict[str, str]
    operating_condition_version: str

    @model_validator(mode="after")
    def units_complete(self):
        if set(self.parameter_units) != set(self.pid.root):
            raise ValueError("Parameter units must match parameter names")
        return self


class SeriesArtifact(Model):
    series_ref: ID
    sha256: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    sample_count: Annotated[int, Field(gt=0, le=1000)]
    start_at: Time
    end_at: Time
    duration_seconds: Number


class SeriesPoint(Model):
    timestamp: Time
    elapsed_seconds: Annotated[Number, Field(ge=0)]
    pressure: Number | None
    target_pressure: Number
    quality: Literal["GOOD", "MISSING", "BAD"]

    @model_validator(mode="after")
    def check_quality(self):
        if self.quality == "GOOD" and self.pressure is None:
            raise ValueError("GOOD requires pressure")
        return self


class ObservationSnapshot(Envelope):
    snapshot_id: ID
    control_loop_id: str
    as_of: Time
    window_start: Time
    window_end: Time
    sample_period_seconds: Number
    source_kind: SourceKind
    adapter_id: str
    adapter_version: str
    pressure_unit: str
    operating_condition_version: str
    pid: PidSnapshot
    series: SeriesArtifact


class QualityReport(Model):
    valid: bool
    sample_count: int
    missing_count: int
    bad_count: int
    reason_codes: Reasons


class Metrics(Model):
    metrics_version: str
    signed_steady_error: Number
    steady_state_error: Number
    iae: Number
    overshoot_ratio: Number
    pressure_std: Number
    settling_time_s: Number | None
    unavailable_reasons: Reasons


class EvaluationDecision(Envelope):
    accepted: bool
    target_reached: bool
    hard_constraints_passed: bool
    score: Number | None
    reason_code: str
    metrics: Metrics | None
