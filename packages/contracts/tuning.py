"""Separate public and persisted contracts for offline steam-cycle tuning."""

from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints

from .agents import ControlOutput
from .common import ID, Envelope, Model, Number, SourceKind, Time

TuningStatus = Literal[
    "QUEUED", "BASELINE_RUNNING", "SEARCHING", "AGENT_RUNNING", "TRIAL_RUNNING",
    "COMPLETED", "NO_SAFE_CANDIDATE", "AGENT_ABSTAINED", "FAILED", "CANCELLED",
]


class ComponentAging(Model):
    """Dimensionless wear severity: 0 is nominal, 1 is maximum modeled wear."""

    model_config = {"frozen": True}
    feedwater_pump: Number = Field(default=0.0, ge=0, le=1)
    feedwater_valve: Number = Field(default=0.0, ge=0, le=1)
    boiler: Number = Field(default=0.0, ge=0, le=1)
    turbine: Number = Field(default=0.0, ge=0, le=1)
    condenser: Number = Field(default=0.0, ge=0, le=1)


class PlantState(Model):
    model_config = {"frozen": True}
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)] = "B 回路状态"
    aging: ComponentAging = Field(default_factory=ComponentAging)


class TuningStart(Envelope):
    plant_state: PlantState = Field(default_factory=PlantState)


class TuningTrial(Model):
    trial_id: ID
    iteration: int
    pid: dict[str, Number]
    result: dict[str, Any]
    traces: dict[str, str]
    trace_hashes: dict[str, str]
    accepted: bool
    reason: str
    agent_output: ControlOutput | None = None
    agent_session_id: str | None = None


class TuningRun(Envelope):
    run_id: ID
    state_version: int = 0
    status: TuningStatus
    source_kind: SourceKind
    created_at: Time
    updated_at: Time
    model_version: str
    scenario_version: str
    plant_state: PlantState = Field(default_factory=PlantState)
    plant_state_hash: str = ""
    assumptions: dict[str, Any]
    events: dict[str, list[dict[str, Any]]]
    baseline_pid: dict[str, Number]
    best_pid: dict[str, Number] | None
    best_trial_id: ID | None
    trials: list[TuningTrial]
    rejections: list[dict[str, Any]] = Field(default_factory=list)
    max_iterations: int
    no_improvement_count: int = 0
    pending_iteration: int | None = None
    session_id: str | None = None
    request_id: ID | None = None
    agent_sent: bool = False
    workflow_version: str | None = None
    prepared_skill_hashes: dict[str, str] = Field(default_factory=dict)
    context_receipt: str | None = None
    context_delivered: bool = False
    agent_attempt_no: int = 0
    agent_failures: list[dict[str, Any]] = Field(default_factory=list, max_length=36)
    search_reviews: list[dict[str, Any]] = Field(default_factory=list, max_length=1)
    deadline_at: Time
    pending_output: ControlOutput | None = None
    cancel_requested: bool = False
    error: str | None = None


class TuningRuns(Envelope):
    items: list[TuningRun]


class TuningTrace(Envelope):
    run_id: ID
    trial_id: ID
    scenario_id: str
    source_kind: SourceKind
    points: list[dict[str, Number]] = Field(max_length=1000)
