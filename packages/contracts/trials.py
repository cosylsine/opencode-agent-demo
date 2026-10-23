from typing import Annotated, Literal

from pydantic import Field, model_validator

from .common import (
    ID,
    PID,
    Constraints,
    Envelope,
    ErrorDetail,
    MetricsConfig,
    Number,
    SourceKind,
    Time,
)
from .observations import SeriesArtifact

TrialStatus = Literal["QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "UNKNOWN"]


class EvaluationContext(Envelope):
    context_id: ID
    snapshot_id: ID
    scenario_id: str
    scenario_version: str
    source_kind: SourceKind
    adapter_id: str
    adapter_version: str
    program_version: str
    target_pressure: Number
    pressure_unit: str
    pid_form: str
    start_at: Time
    duration_seconds: Number
    sample_period_seconds: Number
    seed: int
    metrics_config: MetricsConfig
    constraints: Constraints
    context_hash: str


class TrialRequest(Envelope):
    run_id: ID
    iteration_id: ID | None
    candidate_id: ID | None
    control_loop_id: str
    trace_id: ID
    purpose: Literal["BASELINE", "CANDIDATE"]
    idempotency_key: str
    candidate_pid: PID
    evaluation_context_ref: ID
    evaluation_context_hash: str
    request_hash: str
    timeout_seconds: Annotated[int, Field(gt=0, le=120)]

    @model_validator(mode="after")
    def association(self):
        if self.purpose == "BASELINE":
            if self.iteration_id is not None or self.candidate_id is not None:
                raise ValueError("BASELINE must not have candidate/iteration")
        elif self.iteration_id is None or self.candidate_id is None:
            raise ValueError("CANDIDATE requires candidate/iteration")
        return self


class TrialRecord(Envelope):
    trial_id: ID
    request: TrialRequest
    status: TrialStatus
    source_kind: SourceKind
    adapter_id: str
    adapter_version: str
    created_at: Time
    started_at: Time | None
    finished_at: Time | None
    series: SeriesArtifact | None
    error: ErrorDetail | None

    @model_validator(mode="after")
    def result(self):
        if (self.status == "COMPLETED") != (self.series is not None):
            raise ValueError("Only COMPLETED must contain series")
        if self.status == "FAILED" and self.error is None:
            raise ValueError("FAILED requires error")
        return self
