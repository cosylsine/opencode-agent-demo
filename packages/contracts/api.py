from typing import Annotated, Literal

from pydantic import Field

from .agents import ControlOutput, MonitorOutput
from .common import (
    ID,
    PID,
    ComponentHealth,
    Envelope,
    ErrorDetail,
    Model,
    Number,
    SourceKind,
    Text,
    Time,
)
from .observations import Metrics, PidSnapshot, SeriesPoint
from .trials import TrialStatus

RunStatus = Literal[
    "CREATED",
    "DATA_LOADING",
    "MONITOR_REQUESTED",
    "MONITOR_COMPLETED",
    "BASELINE_REQUESTED",
    "BASELINE_RUNNING",
    "BASELINE_COMPLETED",
    "CANDIDATE_REQUESTED",
    "CANDIDATE_VALIDATED",
    "TRIAL_REQUESTED",
    "TRIAL_RUNNING",
    "TRIAL_COMPLETED",
    "EVALUATING",
    "CANDIDATE_ACCEPTED",
    "CANDIDATE_REJECTED",
    "NO_TUNING_REQUIRED",
    "MANUAL_REVIEW_REQUIRED",
    "SUCCEEDED",
    "STOPPED_NO_IMPROVEMENT",
    "STOPPED_MAX_ITERATIONS",
    "STOPPED_TIMEOUT",
    "FAILED_FINAL",
    "CANCELLED",
]


class TriggerRequest(Envelope):
    scenario_id: Annotated[str, Field(max_length=80)]


class TriggerResponse(Envelope):
    trigger_id: ID
    run_id: ID
    coalesced: bool
    status_url: str


class ChatSession(Envelope):
    session_id: ID
    title: Annotated[str, Field(max_length=80)]
    created_at: Time


class ChatSessions(Envelope):
    items: Annotated[list[ChatSession], Field(max_length=100)]


class ChatSessionRename(Envelope):
    title: Annotated[str, Field(min_length=1, max_length=80)]


class ChatMessage(Model):
    message_id: ID
    session_id: ID
    role: Literal["user", "assistant", "system"]
    content: Annotated[str, Field(max_length=6000)]
    created_at: Time
    run_id: ID | None = None
    status: Literal["complete", "pending", "error"] = "complete"


class ChatMessages(Envelope):
    items: Annotated[list[ChatMessage], Field(max_length=200)]


class ChatRequest(Envelope):
    message: Annotated[str, Field(min_length=1, max_length=2000)]
    scenario_id: Annotated[str, Field(max_length=80)] = "improvable"


class ChatTurn(Envelope):
    user_message: ChatMessage
    response: ChatMessage


class Scenario(Model):
    scenario_id: str
    name: str
    description: str
    source_kind: SourceKind


class Scenarios(Envelope):
    items: Annotated[list[Scenario], Field(max_length=10)]


class AvailableModel(Model):
    model_id: str
    provider: str
    ready: bool


class AvailableModels(Envelope):
    items: list[AvailableModel]


class CatalogAgent(Model):
    name: str
    description: str
    mode: str
    skills: list[str]
    tools: list[str]


class CatalogSkill(Model):
    name: str
    title: str
    description: str
    triggers: list[str]
    agents: list[str]
    registered: bool
    deleted: bool = False
    deletable: bool = False


class CatalogTool(Model):
    name: str
    description: str
    agents: list[str]


class CatalogResponse(Envelope):
    agents: list[CatalogAgent]
    skills: list[CatalogSkill]
    deleted_skills: list[CatalogSkill]
    tools: list[CatalogTool]
    template: str


class SkillRegistration(Envelope):
    title: Annotated[str, Field(min_length=1, max_length=80)]
    description: Annotated[str, Field(min_length=1, max_length=300)]
    triggers: Annotated[list[Annotated[str, Field(min_length=1, max_length=40)]], Field(min_length=1, max_length=12)]
    instructions: Annotated[str, Field(max_length=4000)] = ""


class SkillNamePreview(Envelope):
    name: str


class LiveTelemetryStatus(Envelope):
    scenario_id: str
    running: bool
    window_ready: bool
    latest_sample_at: Time | None
    pressure: Number | None
    target_pressure: Number
    sample_kind: Literal["LIVE", "SIMULATED_BACKFILL"] | None
    jsonl_path: str


class IterationDetail(Model):
    iteration_id: ID
    iteration_no: int
    candidate_pid: PID | None
    trial_status: TrialStatus | None
    series_ref: ID | None
    metrics: Metrics | None
    score: Number | None
    accepted: bool | None
    reason_code: str | None
    control_output: ControlOutput | None


class RunDetail(Envelope):
    run_id: ID
    control_loop_id: str
    scenario_id: str
    status: RunStatus
    state_version: int
    source_kind: SourceKind
    created_at: Time
    finished_at: Time | None
    actual_pid: PidSnapshot | None
    best_pid: PID | None
    best_metrics: Metrics | None
    best_score: Number | None
    observation_series_ref: ID | None
    baseline_series_ref: ID | None
    monitor_output: MonitorOutput | None
    iterations: Annotated[list[IterationDetail], Field(max_length=5)]
    stop_reason: str | None
    error: ErrorDetail | None
    cancel_requested: bool = False
    parent_run_id: ID | None = None


class RunList(Envelope):
    items: Annotated[list[RunDetail], Field(max_length=100)]
    next_cursor: str | None


class SeriesResponse(Envelope):
    series_ref: ID
    source_kind: SourceKind
    pressure_unit: str
    original_sample_count: int
    returned_sample_count: int
    downsampled: bool
    points: Annotated[list[SeriesPoint], Field(max_length=1000)]


class HealthResponse(Envelope):
    live: bool
    ready: bool
    degraded: bool
    components: Annotated[list[ComponentHealth], Field(max_length=20)]


class ErrorResponse(Envelope):
    error: ErrorDetail


class EmptyRequest(Envelope):
    pass


class ReviewRequest(Envelope):
    action: Literal["ACKNOWLEDGE", "REQUEST_RECHECK"]
    comment: Text


class SchedulerConfig(Envelope):
    enabled: bool
    interval_seconds: Annotated[int, Field(ge=30, le=86400)]
    scenario_id: str


class AuditEvent(Model):
    event_id: int
    run_id: ID
    created_at: Time
    event: str
    detail: dict[str, str | int | bool | None]


class EventsResponse(Envelope):
    items: Annotated[list[AuditEvent], Field(max_length=200)]
    next_event_id: int | None


class CsrfResponse(Envelope):
    token: str


class IterationsResponse(Envelope):
    items: Annotated[list[IterationDetail], Field(max_length=5)]


class LoopStatusResponse(Envelope):
    control_loop_id: str
    active_run_id: ID | None
