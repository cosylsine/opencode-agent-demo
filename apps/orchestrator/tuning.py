"""Durable, independent Control Agent workflow for offline steam-cycle B."""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from packages.adapters.control_workflow import (
    REQUIRED_SKILLS,
    WORKFLOW_VERSION,
    ControlWorkflow,
    load_control_skills,
)
from packages.adapters.storage import atomic_write
from packages.contracts.agents import ControlOutput
from packages.contracts.common import SourceKind, now, uid
from packages.contracts.tuning import PlantState, TuningRun, TuningTrial
from packages.domain.core import DomainError
from packages.simulation.evaluate import SCORE_VERSION, WEIGHTS, evaluate_pid
from packages.simulation.plant import (
    ASSUMPTIONS,
    BASE_PID,
    PID_BOUNDS,
    SCENARIO_VERSION,
    VERSION,
    model_parameters,
    plant_state_hash,
    scenario_events,
)
from packages.simulation.search import INITIAL_CANDIDATES, SEARCH_POLICY_VERSION, search_feedback

MAX_ITERATIONS = 12
MAX_NO_IMPROVEMENT = 4
MIN_IMPROVEMENT = 0.00001
MAX_AGENT_ATTEMPTS = 3
TERMINAL = {"COMPLETED", "NO_SAFE_CANDIDATE", "AGENT_ABSTAINED", "FAILED", "CANCELLED"}


def model_metrics(value):
    """Bound numeric verbosity for inference; persisted results retain full precision."""
    if isinstance(value, float):
        return float(format(value, ".8g"))
    if isinstance(value, dict):
        return {key: model_metrics(item) for key, item in value.items()}
    if isinstance(value, list):
        return [model_metrics(item) for item in value]
    return value


def new_run(plant_state: PlantState | None = None) -> TuningRun:
    timestamp = now()
    state = (plant_state or PlantState()).model_copy(deep=True)
    return TuningRun(
        schema_version="1.0", run_id=uid(), source_kind=SourceKind.SIMULATION,
        status="QUEUED", created_at=timestamp, updated_at=timestamp,
        model_version=VERSION, scenario_version=SCENARIO_VERSION,
        plant_state=state, plant_state_hash=plant_state_hash(state),
        assumptions={**ASSUMPTIONS, "pid_bounds": PID_BOUNDS, "score_version": SCORE_VERSION,
                     "search_policy_version": SEARCH_POLICY_VERSION,
                     "effective_parameters": model_parameters(state),
                     "score_weights": WEIGHTS, "aggregation": "0.7 × 平均分 + 0.3 × 最差工况分"},
        events=scenario_events(), baseline_pid=BASE_PID.copy(), best_pid=None,
        best_trial_id=None, trials=[], rejections=[], max_iterations=MAX_ITERATIONS,
        deadline_at=(datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat().replace("+00:00", "Z"),
    )


def validate_candidate(output: ControlOutput, best: dict, seen: list[dict]) -> dict:
    if output.decision != "PROPOSE" or output.candidate_pid is None or output.delta is None:
        raise DomainError("CANDIDATE_INVALID")
    candidate = output.candidate_pid.root
    delta = output.delta.root
    if set(candidate) != set(PID_BOUNDS) or set(delta) != set(PID_BOUNDS):
        raise DomainError("CANDIDATE_INVALID", "候选参数名不完整")
    for key, value in candidate.items():
        low, high = PID_BOUNDS[key]
        if not low <= value <= high or abs(value - best[key] - delta[key]) > 1e-6:
            raise DomainError("CANDIDATE_INVALID", "参数边界或 delta 不一致")
    if any(all(abs(candidate[key] - old[key]) <= 1e-6 for key in PID_BOUNDS) for old in seen):
        raise DomainError("CANDIDATE_INVALID", "候选参数重复")
    return candidate.copy()


class TuningEngine:
    def __init__(self, db, client, tokens, catalog_root=None):
        self.db, self.client, self.tokens = db, client, tokens
        self.catalog_root = Path(catalog_root) if catalog_root else Path(__file__).parents[2]
        self.wake = asyncio.Event()

    @staticmethod
    def prompt_payload(run: TuningRun, request_id: str, context=None) -> dict:
        # The program performs one MCP read and includes that result once.
        return {
            "schema_version": "1.0", "source_kind": "SIMULATION",
            "run_id": run.run_id, "control_loop_id": "feedwater-pump-sim-b",
            "request_id": request_id,
            "skills_preloaded": list(REQUIRED_SKILLS),
            "workflow_version": run.workflow_version,
            "workflow_feedback": run.agent_failures[-1]["error_code"] if run.agent_failures else None,
            "control_context": context,
            "task": "两项技能正文和本轮授权 MCP 上下文已由程序提供，无须再调用读取工具。"
                    "回路老化状态已固定，只能调整 PID，不能修改部件老化或放宽安全边界。"
                    "依据全部历史与拒绝记录，为四种负荷变化提出同一组给水泵 PID。"
                    "最后调用 StructuredOutput 并原样返回工具提供的 context_receipt；流程异常填 WORKFLOW_ERROR。",
        }

    async def prepare_attempt(self, run):
        skills = load_control_skills(self.catalog_root)
        run.request_id = uid()
        run.session_id = await self.client.create_session(
            f"{run.run_id}/control/{run.pending_iteration}/attempt/{run.agent_attempt_no + 1}"
        )
        run.workflow_version = WORKFLOW_VERSION
        run.prepared_skill_hashes = {name: item["sha256"] for name, item in skills.items()}
        run.context_receipt = uid()
        run.context_delivered = False
        run.agent_sent = False
        run.agent_attempt_no += 1
        run.status = "AGENT_RUNNING"
        with self.db.engine.begin() as conn:
            self.db.save_tuning(run, conn)
            self.db.audit(run.run_id, "CONTROL_SKILLS_PRELOADED", {
                "request_id": run.request_id, "session_id": run.session_id,
                "iteration": run.pending_iteration, "attempt": run.agent_attempt_no,
                "skill_hashes": run.prepared_skill_hashes,
            }, conn)

    def read_context(self, scope):
        # Persist delivery and its audit together, including the receipt needed
        # for result validation after a process restart.
        with self.db.engine.begin() as conn:
            run = self.db.get_tuning(scope.run_id, conn)
            if (run.status != "AGENT_RUNNING" or run.cancel_requested or now() >= run.deadline_at
                or run.request_id != scope.request_id
                or str(run.pending_iteration) != scope.iteration_id):
                raise DomainError("TOOL_FORBIDDEN", status=403)
            if run.workflow_version == WORKFLOW_VERSION:
                if (not run.context_receipt
                    or set(run.prepared_skill_hashes) != set(REQUIRED_SKILLS)):
                    raise DomainError("CONTROL_NOT_PREPARED", "调优请求尚未完成程序准备", 409)
                if run.context_delivered:
                    self.db.audit(run.run_id, "MCP_READ_DUPLICATE", {"request_id": scope.request_id}, conn)
                    return {"schema_version": "1.0", "data": {
                        "context_already_delivered": True, "context_receipt": run.context_receipt,
                        "instruction": "请使用本次会话首次读取的完整上下文，不要重复调用。",
                    }}
            payload = self.payload(run, scope.request_id, "[REDACTED TOKEN]")
            payload.pop("call_context_token", None)
            if run.workflow_version == WORKFLOW_VERSION:
                payload["context_receipt"] = run.context_receipt
                run.context_delivered = True
                self.db.save_tuning(run, conn)
            self.db.audit(run.run_id, "MCP_READ", {
                "request_id": scope.request_id, "tool": "get_tuning_context", "role": "control",
            }, conn)
            return {"schema_version": "1.0", "data": payload}

    async def request_control(self, run):
        prepared = run.workflow_version == WORKFLOW_VERSION
        workflow = ControlWorkflow(run.context_receipt) if prepared else None
        if run.agent_sent:
            return await self.client.recover(
                run.session_id, ControlOutput, "control", restarting=True,
                require_chinese=True, workflow=workflow,
            )
        skills = load_control_skills(self.catalog_root)
        if prepared and {name: item["sha256"] for name, item in skills.items()} != run.prepared_skill_hashes:
            raise DomainError("CONTROL_SKILLS_UNAVAILABLE", "准备后技能文件发生变化，尚未发起模型请求", 503)
        token = self.tokens.issue(run.run_id, run.request_id, str(run.pending_iteration), "control", ttl=360)
        try:
            if prepared:
                workflow = ControlWorkflow(run.context_receipt, skills, lambda: self.read_context(
                    self.tokens.authorize(token, "get_tuning_context")
                ))
            context = await self.client.control_context(token, workflow)
            run = self.db.get_tuning(run.run_id)
            if run.cancel_requested:
                raise DomainError("CONTROL_PRE_SEND_CANCELLED")
            if now() >= run.deadline_at:
                raise DomainError("CONTROL_TIME_BUDGET_EXHAUSTED")
            if (not run.context_delivered or context.get("context_receipt") != run.context_receipt
                or context.get("context_already_delivered") is True):
                raise DomainError("CONTROL_CONTEXT_UNAVAILABLE", "本轮授权上下文尚未准备完成", 503)
            payload = self.prompt_payload(run, run.request_id, context)
            run.agent_sent = True
            self.db.save_tuning(run)
            return await self.client.prompt(run.session_id, "control", payload, ControlOutput, workflow=workflow)
        finally:
            self.tokens.revoke(token)

    def reject_workflow_attempt(self, run, error):
        run.agent_failures.append({
            "iteration": run.pending_iteration, "attempt": run.agent_attempt_no,
            "request_id": run.request_id, "session_id": run.session_id,
            "error_code": error.code,
        })
        with self.db.engine.begin() as conn:
            self.db.save_tuning(run, conn)
            self.db.audit(run.run_id, "CONTROL_WORKFLOW_REJECTED", run.agent_failures[-1], conn)
        if run.agent_attempt_no >= MAX_AGENT_ATTEMPTS:
            raise DomainError("CONTROL_WORKFLOW_FAILED", f"同一轮准备或输出校验连续失败，已达到 3 次尝试上限（{error.code}）", 503)
        run.session_id = None
        run.request_id = None
        run.agent_sent = False
        run.context_delivered = False
        run.context_receipt = None
        run.prepared_skill_hashes = {}
        self.db.save_tuning(run)

    def payload(self, run: TuningRun, request_id: str, token: str) -> dict:
        best = next((t for t in run.trials if t.trial_id == run.best_trial_id), None)
        return {
            "schema_version": "1.0", "source_kind": "SIMULATION",
            "run_id": run.run_id, "control_loop_id": "feedwater-pump-sim-b",
            "request_id": request_id, "call_context_token": token,
            "task": "为全部四种负荷变化提出同一组给水泵 PID 参数。泵跟踪给水流量；安全由程序裁决。",
            "model_version": run.model_version, "scenario_version": run.scenario_version,
            "plant_state": run.plant_state.model_dump(mode="json"),
            "plant_state_hash": run.plant_state_hash,
            "effective_parameters": run.assumptions.get("effective_parameters", {}),
            "search_feedback": model_metrics(search_feedback(run)),
            "aging_scale": ASSUMPTIONS["aging_scale"],
            "aging_policy": ASSUMPTIONS["aging_policy"],
            "initial_state_policy": ASSUMPTIONS["initial_state_policy"],
            "pid_form": ASSUMPTIONS["pump_pid_form"], "bounds": PID_BOUNDS,
            "actual_pid": run.baseline_pid,
            "actual_pid_source": "SIMULATION_BASELINE",
            "baseline_pid": run.baseline_pid,
            "current_best": {"pid": run.best_pid or run.baseline_pid,
                             "safe": best is not None,
                             "result": model_metrics(best.result if best else run.trials[0].result if run.trials else None)},
            "min_improvement": MIN_IMPROVEMENT,
            "metrics_precision": "指标为 8 位有效数字，接受判定使用服务端原始精度；PID 不作舍入。",
            "remaining_iterations": run.max_iterations - (run.pending_iteration or 1) + 1,
            "deadline_at": run.deadline_at,
            "remaining_seconds": max(0, (datetime.fromisoformat(run.deadline_at.replace("Z", "+00:00"))
                                         - datetime.now(timezone.utc)).total_seconds()),
            "no_improvement_count": run.no_improvement_count,
            "events": run.events,
            "history": [
                {"iteration": t.iteration, "pid": t.pid, "result": model_metrics(t.result),
                 "accepted": t.accepted, "reason": t.reason}
                for t in run.trials
            ],
            "rejections": [
                {**{"iteration": item["iteration"], "reason": item["reason"]},
                 **{key: item["agent_output"][key] for key in ("candidate_pid", "delta")
                    if key in (item.get("agent_output") or {})}}
                for item in run.rejections
            ],
        }

    def trace_path(self, run_id: str, trial_id: str, scenario_id: str) -> Path:
        return self.db.root / "simulation" / run_id / trial_id / f"{scenario_id}.json"

    def trial(self, run: TuningRun, pid: dict, iteration: int, output: ControlOutput | None):
        if run.plant_state_hash and run.plant_state_hash != plant_state_hash(run.plant_state):
            raise DomainError("SIMULATION_STATE_MISMATCH", "回路状态与本次调优固定的状态不一致")
        result, traces = evaluate_pid(pid, run.plant_state)
        trial_id = uid()
        refs, hashes = {}, {}
        for scenario, rows in traces.items():
            path = self.trace_path(run.run_id, trial_id, scenario)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            data = json.dumps(rows, separators=(",", ":")).encode()
            atomic_write(path, data)
            refs[scenario] = f"{trial_id}/{scenario}"
            hashes[scenario] = hashlib.sha256(data).hexdigest()
        best = next((t for t in run.trials if t.trial_id == run.best_trial_id), None)
        accepted = result["safe"] and (best is None or result["score"] < best.result["score"] - MIN_IMPROVEMENT)
        reason = "IMPROVED" if accepted else "SAFETY_VIOLATION" if not result["safe"] else "NO_IMPROVEMENT"
        item = TuningTrial(trial_id=trial_id, iteration=iteration, pid=pid, result=result,
                           traces=refs, trace_hashes=hashes, accepted=accepted,
                           reason=reason, agent_output=output,
                           agent_session_id=run.session_id if output else None)
        run.trials.append(item)
        if accepted:
            run.best_pid, run.best_trial_id = pid.copy(), trial_id
            run.no_improvement_count = 0
        elif iteration:
            run.no_improvement_count += 1
        self.db.save_tuning(run)
        self.db.audit(run.run_id, "SIMULATION_TRIAL", {"trial_id": trial_id, "iteration": iteration,
                      "accepted": accepted, "safe": result["safe"]})

    async def step(self, run: TuningRun):
        if run.cancel_requested:
            run.status = "CANCELLED"
            self.db.save_tuning(run)
            self.tokens.revoke_run(run.run_id)
            return
        if now() >= run.deadline_at:
            run.status = "COMPLETED" if run.best_pid else "NO_SAFE_CANDIDATE"
            run.error = "调优时间预算已用完"
            self.db.save_tuning(run)
            self.tokens.revoke_run(run.run_id)
            return
        if run.status == "QUEUED":
            run.status = "BASELINE_RUNNING"
            self.db.save_tuning(run)
        elif run.status == "BASELINE_RUNNING":
            if not run.trials:
                self.trial(run, run.baseline_pid, 0, None)
            run.status = "SEARCHING"
            self.db.save_tuning(run)
        elif run.status == "SEARCHING":
            completed = sum(t.iteration > 0 for t in run.trials) + len(run.rejections)
            if completed >= run.max_iterations or run.no_improvement_count >= MAX_NO_IMPROVEMENT:
                run.status = "COMPLETED" if run.best_pid else "NO_SAFE_CANDIDATE"
                self.db.save_tuning(run)
                return
            if not self.client.ready:
                raise DomainError("DEPENDENCY_NOT_READY", "Control Agent 不可用")
            run.pending_iteration = completed + 1
            run.agent_attempt_no = 0
            await self.prepare_attempt(run)
        elif run.status == "AGENT_RUNNING":
            if not run.session_id or (run.workflow_version is None and not run.agent_sent):
                await self.prepare_attempt(run)
                return
            try:
                output = await self.request_control(run)
            except DomainError as exc:
                current = self.db.get_tuning(run.run_id)
                if current.cancel_requested:
                    current.status = "CANCELLED"
                    self.db.save_tuning(current)
                    return
                if now() >= current.deadline_at:
                    current.status = "COMPLETED" if current.best_pid else "NO_SAFE_CANDIDATE"
                    current.error = "调优时间预算已用完"
                    self.db.save_tuning(current)
                    return
                if exc.code not in {"CONTROL_WORKFLOW_INVALID", "AGENT_OUTPUT_INVALID", "CONTROL_CONTEXT_UNAVAILABLE"}:
                    raise
                self.reject_workflow_attempt(current, exc)
                return
            current = self.db.get_tuning(run.run_id)
            if current.cancel_requested:
                current.status = "CANCELLED"
                self.db.save_tuning(current)
                return
            run = current
            if run.workflow_version == WORKFLOW_VERSION and not run.context_delivered:
                self.reject_workflow_attempt(run, DomainError("CONTROL_WORKFLOW_INVALID", "尚未读取本轮授权上下文"))
                return
            if output.decision == "ABSTAIN":
                feedback = search_feedback(run)
                if (run.workflow_version == WORKFLOW_VERSION and run.trials
                    and feedback["evaluated_candidates"] < INITIAL_CANDIDATES
                    and not feedback["uncontrollable_pressure_violation_scenarios"]
                    and not run.search_reviews and run.agent_attempt_no < MAX_AGENT_ATTEMPTS):
                    review = {
                        "iteration": run.pending_iteration, "reason": output.reason,
                        "request_id": run.request_id, "session_id": run.session_id,
                    }
                    run.search_reviews.append(review)
                    run.session_id = None
                    run.request_id = None
                    run.agent_sent = False
                    run.context_delivered = False
                    run.context_receipt = None
                    run.prepared_skill_hashes = {}
                    with self.db.engine.begin() as conn:
                        self.db.save_tuning(run, conn)
                        self.db.audit(run.run_id, "CONTROL_EARLY_ABSTAIN_REVIEW", review, conn)
                    return
                run.status = "AGENT_ABSTAINED" if run.best_pid else "NO_SAFE_CANDIDATE"
                run.error = output.reason if run.best_pid else "没有合规候选；Control Agent 放弃继续提案。" + output.reason
                self.db.audit(run.run_id, "CONTROL_ABSTAIN", {"iteration": run.pending_iteration})
            else:
                run.pending_output = output
                run.status = "TRIAL_RUNNING"
                self.db.audit(run.run_id, "CONTROL_PROPOSE", {"iteration": run.pending_iteration})
            self.db.save_tuning(run)
        elif run.status == "TRIAL_RUNNING":
            output = run.pending_output
            try:
                pid = validate_candidate(output, run.best_pid or run.baseline_pid, [t.pid for t in run.trials])
            except DomainError as exc:
                run.no_improvement_count += 1
                run.rejections.append({"iteration": run.pending_iteration, "reason": exc.message,
                                       "agent_output": output.model_dump(mode="json"),
                                       "agent_session_id": run.session_id})
                self.db.audit(run.run_id, "CONTROL_REJECTED", {"iteration": run.pending_iteration,
                              "reason_code": exc.code})
            else:
                self.trial(run, pid, run.pending_iteration, output)
            run.pending_output = None
            run.pending_iteration = None
            run.session_id = None
            run.request_id = None
            run.agent_sent = False
            run.status = "SEARCHING"
            self.db.save_tuning(run)

    async def serve(self):
        while True:
            run = self.db.active_tuning()
            if run is None:
                self.wake.clear()
                try:
                    await asyncio.wait_for(self.wake.wait(), 5)
                except asyncio.TimeoutError:
                    pass
                continue
            try:
                await self.step(run)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                latest = self.db.get_tuning(run.run_id)
                if latest.status not in TERMINAL:
                    latest.status = "CANCELLED" if latest.cancel_requested else "FAILED"
                    latest.error = (f"{exc.code}: {exc.message}" if isinstance(exc, DomainError)
                                    and exc.code != exc.message else str(exc))[:300]
                    self.db.save_tuning(latest)
                    self.db.audit(run.run_id, "TUNING_FAILED", {
                        "iteration": latest.pending_iteration,
                        "error_code": exc.code if isinstance(exc, DomainError) else "INTERNAL_ERROR",
                    })
                    self.tokens.revoke_run(run.run_id)
            await asyncio.sleep(0)
