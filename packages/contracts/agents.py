from typing import Annotated, Literal

from pydantic import Field, model_validator

from .common import ID, PID, Constraints, Envelope, Model, Number, Reasons, SourceKind, Text
from .observations import Metrics, PidSnapshot, QualityReport

Assessment = Literal[
    "NORMAL_STABLE",
    "STEADY_HIGH",
    "STEADY_LOW",
    "EXCESSIVE_OVERSHOOT",
    "SUSTAINED_OSCILLATION",
    "SLOW_RESPONSE",
    "OPERATING_CONDITION_CHANGED",
    "DATA_ANOMALY",
    "UNCERTAIN",
]


class RuleAssessment(Model):
    assessment: Assessment
    reason_codes: Reasons


class MonitorPolicy(Model):
    allowed_assessments: Annotated[list[Assessment], Field(max_length=9)]
    confidence_threshold: Number
    target_error: Number
    target_overshoot: Number
    target_std: Number
    target_settling: Number


class AgentRequest(Envelope):
    request_id: ID
    run_id: ID
    control_loop_id: str
    source_kind: SourceKind
    call_context_token: Annotated[str, Field(min_length=16, repr=False)]


class MonitorRequest(AgentRequest):
    snapshot_id: ID
    target_pressure: Number
    pressure_unit: str
    actual_pid: PidSnapshot
    quality: QualityReport
    metrics: Metrics
    rule_assessment: RuleAssessment
    policy: MonitorPolicy


class Evidence(Model):
    feature: Text
    value: Number
    interpretation: Text


class MonitorOutput(Envelope):
    assessment: Assessment
    confidence: Annotated[Number, Field(ge=0, le=1)]
    needs_tuning: bool
    manual_review_required: bool
    evidence: Annotated[list[Evidence], Field(max_length=10)]
    reason: Text
    warnings: Reasons


class Best(Model):
    pid: PID
    metrics: Metrics
    score: Number


class HistoryItem(Model):
    iteration_no: int
    candidate_pid: PID
    metrics: Metrics | None
    score: Number | None
    accepted: bool
    reason_code: str


class ControlRequest(AgentRequest):
    iteration_id: ID
    candidate_id: ID
    actual_pid: PidSnapshot
    current_best: Best
    observation_metrics: Metrics
    observation_state: RuleAssessment
    observation_window_end: str
    evaluation_context_hash: str
    constraints: Constraints
    remaining_iterations: Annotated[int, Field(ge=0, le=5)]
    history: Annotated[list[HistoryItem], Field(max_length=5)]
    example_program_description: Text


class ControlOutput(Envelope):
    decision: Literal["PROPOSE", "ABSTAIN"]
    delta: PID | None
    candidate_pid: PID | None
    reason: Text
    expected_effect: Text
    risk_notes: Reasons

    @model_validator(mode="after")
    def proposal(self):
        if self.decision == "PROPOSE" and (self.delta is None or self.candidate_pid is None):
            raise ValueError("PROPOSE requires delta and candidate_pid")
        if self.decision == "ABSTAIN" and (
            self.delta is not None or self.candidate_pid is not None
        ):
            raise ValueError("ABSTAIN requires null delta and candidate_pid")
        return self
