from typing import Annotated, Literal

from pydantic import Field

from .agents import ControlOutput, MonitorOutput
from .api import RunDetail
from .common import ID, Envelope, Time
from .observations import ObservationSnapshot
from .trials import EvaluationContext


class RunRecord(RunDetail):
    started_at: Time | None = None
    deadline: Time | None = None
    snapshot: ObservationSnapshot | None = None
    context: EvaluationContext | None = None
    baseline_trial_id: ID | None = None
    candidate_trial_ids: dict[ID, ID] = Field(default_factory=dict)
    no_improvement_count: int = 0
    max_iterations: Annotated[int, Field(ge=1, le=5)] = 5
    config_fingerprint: str
    runtime_model: str
    knowledge_hash: str


class AgentAttempt(Envelope):
    request_id: ID
    run_id: ID
    iteration_id: ID | None
    role: Literal["monitor", "control"]
    status: Literal["PREPARING", "REQUESTED", "COMPLETED", "FAILED", "UNKNOWN", "LATE_RESULT"]
    session_id: str | None
    message_id: str | None
    created_at: Time
    deadline: Time
    payload_hash: str
    payload_redacted: str
    token_hash: str | None = None
    output: MonitorOutput | ControlOutput | None
    error_code: str | None
