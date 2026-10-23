import asyncio
import hashlib
import re
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid5

import httpx

from packages.contracts.agents import (
    Best,
    ControlOutput,
    ControlRequest,
    HistoryItem,
    MonitorOutput,
    MonitorPolicy,
    MonitorRequest,
)
from packages.contracts.api import IterationDetail
from packages.contracts.common import ErrorDetail, SourceKind, now, uid
from packages.contracts.internal import AgentAttempt, RunRecord
from packages.contracts.observations import SnapshotQuery
from packages.contracts.trials import EvaluationContext, TrialRequest
from packages.domain.core import (
    CONFIG,
    CONSTRAINTS,
    DESCRIPTION,
    DomainError,
    assess,
    context_hash,
    digest,
    guard,
    metrics,
    quality,
    reached,
    request_hash,
    safe,
    score,
)
from packages.domain.states import TERMINAL

FINGERPRINT = digest(
    {
        "metrics": CONFIG.model_dump(mode="json"),
        "constraints": CONSTRAINTS.model_dump(mode="json"),
        "program": "example-v2",
    }
)


def knowledge_hash():
    root = Path(__file__).parents[2] / ".opencode"
    files = sorted((*root.glob("agents/*.md"), *root.glob("skills/*/SKILL.md")))
    return hashlib.sha256(b"".join(p.read_bytes() for p in files)).hexdigest()


def make_run(scenario, settings, parent=None):
    return RunRecord(
        schema_version="1.0",
        run_id=uid(),
        control_loop_id="feedwater-pump-demo",
        scenario_id=scenario,
        status="CREATED",
        state_version=0,
        source_kind=SourceKind.EXAMPLE,
        created_at=now(),
        finished_at=None,
        actual_pid=None,
        best_pid=None,
        best_metrics=None,
        best_score=None,
        observation_series_ref=None,
        baseline_series_ref=None,
        monitor_output=None,
        iterations=[],
        stop_reason=None,
        error=None,
        parent_run_id=parent,
        max_iterations=settings.max_iterations,
        config_fingerprint=FINGERPRINT,
        runtime_model=settings.provider + "/" + settings.model,
        knowledge_hash=knowledge_hash(),
    )


class Engine:
    def __init__(self, db, series, source, evaluator, client, tokens, settings, monitor_only=False):
        self.db, self.series, self.source, self.evaluator = db, series, source, evaluator
        self.client, self.tokens, self.settings = client, tokens, settings
        self.monitor_only = monitor_only
        self.pending: AgentAttempt | None = None
        self.wake = asyncio.Event()

    def move(self, run, status, event=None, detail=None):
        run.status = status
        if status in TERMINAL:
            run.finished_at = now()
            self.tokens.revoke_run(run.run_id)
        with self.db.engine.begin() as conn:
            self.db.save(run, run.state_version, event or status, detail, conn)
            if self.pending:
                self.pending.status = "COMPLETED"
                self.db.save_attempt(self.pending, conn)
                self.pending = None

    def stop(self, run, status, code):
        run.stop_reason = code
        if status in ("FAILED_FINAL", "MANUAL_REVIEW_REQUIRED"):
            run.error = ErrorDetail(code=code, message=code, retryable=False, trace_id=uid())
        self.move(run, status)

    async def serve(self):
        while True:
            active = self.db.active()
            if active:
                await self.drive(active[0].run_id)
            else:
                self.wake.clear()
                try:
                    await asyncio.wait_for(self.wake.wait(), 1)
                except asyncio.TimeoutError:
                    pass

    async def drive(self, run_id):
        while True:
            run = self.db.get(run_id)
            if run.status in TERMINAL:
                return
            if run.cancel_requested:
                await self.cancel_external(run)
                self.stop(self.db.get(run_id), "CANCELLED", "USER_CANCELLED")
                return
            if run.deadline and now() >= run.deadline:
                await self.cancel_external(run)
                self.stop(self.db.get(run_id), "STOPPED_TIMEOUT", "RUN_DEADLINE")
                return
            if (
                run.config_fingerprint != FINGERPRINT
                or run.knowledge_hash != knowledge_hash()
                or run.runtime_model != self.settings.provider + "/" + self.settings.model
            ):
                self.stop(run, "MANUAL_REVIEW_REQUIRED", "VERSION_UNAVAILABLE")
                return
            task = asyncio.create_task(self.step(run))
            try:
                while not task.done():
                    await asyncio.wait({task}, timeout=0.1)
                    current = self.db.get(run_id)
                    if current.cancel_requested or (current.deadline and now() >= current.deadline):
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        await self.cancel_external(current)
                        break
                if task.cancelled():
                    continue
                await task
            except asyncio.CancelledError:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise
            except DomainError as exc:
                current = self.db.get(run_id)
                if current.cancel_requested or exc.code in (
                    "CANCEL_REQUESTED",
                    "RUN_STATE_CONFLICT",
                ):
                    continue
                if current.status not in TERMINAL:
                    self.stop(
                        current,
                        "MANUAL_REVIEW_REQUIRED"
                        if exc.code
                        in (
                            "RESULT_UNKNOWN",
                            "DATA_INVALID",
                            "SNAPSHOT_MISMATCH",
                            "CONTROL_ABSTAIN",
                            "CANDIDATE_INVALID",
                            "MONITOR_CONFLICT",
                            "MONITOR_UNCERTAIN",
                        )
                        else "FAILED_FINAL",
                        exc.code,
                    )
                return
            except Exception:
                current = self.db.get(run_id)
                if current.cancel_requested:
                    continue
                self.stop(current, "FAILED_FINAL", "INTERNAL_ERROR")
                raise  # worker supervisor logs traceback; no silent success

    async def cancel_external(self, run):
        self.tokens.revoke_run(run.run_id)
        if self.pending:
            self.pending.status = "LATE_RESULT"
            self.db.save_attempt(self.pending)
            self.db.audit(run.run_id, "LATE_RESULT", {"request_id": self.pending.request_id})
            self.pending = None
        for role in ("monitor", "control"):
            rounds = [None] if role == "monitor" else [i.iteration_id for i in run.iterations]
            for iteration in rounds:
                for attempt in self.db.attempt_list(run.run_id, role, iteration):
                    if attempt.session_id and attempt.status == "REQUESTED":
                        await self.client.abort(attempt.session_id)
        trial_ids = list(run.candidate_trial_ids.values())
        if run.baseline_trial_id:
            trial_ids.append(run.baseline_trial_id)
        for trial_id in trial_ids:
            try:
                trial = await self.evaluator.get(trial_id)
            except DomainError as exc:
                if exc.code == "NOT_FOUND":
                    continue
                raise
            if trial.status in ("QUEUED", "RUNNING"):
                await self.evaluator.cancel(trial.trial_id)

    def history(self, run):
        return [
            HistoryItem(
                iteration_no=i.iteration_no,
                candidate_pid=i.candidate_pid,
                metrics=i.metrics,
                score=i.score,
                accepted=i.accepted,
                reason_code=i.reason_code,
            )
            for i in run.iterations
            if i.accepted is not None and i.candidate_pid
        ]

    def agent_payload(self, run, role, request_id, token):
        common = dict(
            request_id=request_id,
            run_id=run.run_id,
            control_loop_id=run.control_loop_id,
            source_kind=run.source_kind,
            call_context_token=token,
        )
        if role == "monitor":
            points = self.series.read(run.snapshot.series)
            m = metrics(points)
            return MonitorRequest(
                schema_version="1.0",
                **common,
                snapshot_id=run.snapshot.snapshot_id,
                target_pressure=30.0,
                pressure_unit="demo_unit",
                actual_pid=run.actual_pid,
                quality=quality(points),
                metrics=m,
                rule_assessment=assess(m),
                policy=MonitorPolicy(
                    allowed_assessments=["NORMAL_STABLE", "STEADY_HIGH", "STEADY_LOW"],
                    confidence_threshold=0.6,
                    target_error=0.08,
                    target_overshoot=0.02,
                    target_std=0.02,
                    target_settling=40.0,
                ),
            )
        iteration = run.iterations[-1]
        observed = metrics(self.series.read(run.snapshot.series))
        return ControlRequest(
            schema_version="1.0",
            **common,
            iteration_id=iteration.iteration_id,
            candidate_id=str(uuid5(UUID(iteration.iteration_id), "candidate")),
            actual_pid=run.actual_pid,
            current_best=Best(pid=run.best_pid, metrics=run.best_metrics, score=run.best_score),
            observation_metrics=observed,
            observation_state=assess(observed),
            observation_window_end=run.snapshot.window_end,
            evaluation_context_hash=run.context.context_hash,
            constraints=run.context.constraints,
            remaining_iterations=run.max_iterations - iteration.iteration_no + 1,
            history=self.history(run),
            example_program_description=DESCRIPTION,
        )

    async def ask(self, run, role):
        schema = MonitorOutput if role == "monitor" else ControlOutput
        iteration = None if role == "monitor" else run.iterations[-1].iteration_id
        attempts = self.db.attempt_list(run.run_id, role, iteration)
        budget = 2 if role == "monitor" else 3
        # Recover a saved result or a uniquely bound session before any new inference.
        for attempt in attempts:
            if attempt.status in ("REQUESTED", "COMPLETED"):
                if attempt.output is not None:
                    self.pending = attempt
                    return attempt.output
                if not attempt.session_id:
                    raise DomainError("RESULT_UNKNOWN")
                try:
                    output = await self.client.recover(
                        attempt.session_id,
                        schema,
                        role,
                        budget=min(30, self.remaining(run)),
                        restarting=True,
                        require_chinese=True,
                    )
                    if attempt.token_hash and any(
                        self.tokens.key(text) == attempt.token_hash
                        for text in re.findall(r"[A-Za-z0-9_-]{43}", output.model_dump_json())
                    ):
                        raise DomainError("AGENT_OUTPUT_INVALID")
                except DomainError as exc:
                    attempt.status = "UNKNOWN" if exc.code == "RESULT_UNKNOWN" else "FAILED"
                    attempt.error_code = exc.code
                    self.db.save_attempt(attempt)
                    if exc.code == "RESULT_UNKNOWN":
                        raise
                else:
                    attempt.output = output
                    self.db.save_attempt(attempt)
                    self.pending = attempt
                    return output
            elif attempt.status == "PREPARING":
                attempt.status, attempt.error_code = "FAILED", "PRE_SEND_FAILURE"
                self.db.save_attempt(attempt)
        while len(attempts) < budget:
            errors = [a.error_code for a in attempts if a.error_code]
            if errors.count("AGENT_OUTPUT_INVALID") >= 2:
                raise DomainError("AGENT_OUTPUT_INVALID")
            if errors.count("CANDIDATE_INVALID") >= 2:
                raise DomainError("CANDIDATE_INVALID")
            if errors.count("PRE_SEND_FAILURE") >= 2:
                raise DomainError("OPENCODE_UNAVAILABLE")
            if errors.count("AGENT_INFRA_RETRYABLE") >= 2:
                raise DomainError("AGENT_RUNTIME_FAILED")
            request_id = uid()
            token = self.tokens.issue(
                run.run_id, request_id, iteration, role, ttl=min(150, self.remaining(run))
            )
            payload = self.agent_payload(run, role, request_id, token)
            redacted = payload.model_copy(update={"call_context_token": "[REDACTED]"})
            attempt = AgentAttempt(
                schema_version="1.0",
                request_id=request_id,
                run_id=run.run_id,
                iteration_id=iteration,
                role=role,
                status="PREPARING",
                session_id=None,
                message_id=None,
                created_at=now(),
                deadline=run.deadline,
                payload_hash=digest(redacted),
                payload_redacted=redacted.model_dump_json(),
                token_hash=self.tokens.key(token),
                output=None,
                error_code=None,
            )
            self.db.save_attempt(attempt)
            attempts.append(attempt)
            try:
                try:
                    attempt.session_id = await self.client.create_session(
                        f"{run.run_id}/{role}/{iteration}/{request_id}"
                    )
                except (httpx.HTTPError, DomainError):
                    attempt.status, attempt.error_code = "FAILED", "PRE_SEND_FAILURE"
                    self.db.save_attempt(attempt)
                    await asyncio.sleep(2)
                    continue
                self.db.save_attempt(attempt)  # bind before sending
                attempt.status = "REQUESTED"
                self.db.save_attempt(attempt)
                output = await self.client.prompt(
                    attempt.session_id,
                    role,
                    payload.model_dump(mode="json"),
                    schema,
                    correction=", ".join(errors),
                )
                if token in output.model_dump_json():
                    raise DomainError("AGENT_OUTPUT_INVALID")
                attempt.output = output
                self.db.save_attempt(attempt)
                self.pending = attempt
                return output
            except DomainError as exc:
                attempt.status = "UNKNOWN" if exc.code == "RESULT_UNKNOWN" else "FAILED"
                attempt.error_code = exc.code
                self.db.save_attempt(attempt)
                if exc.code == "AGENT_INFRA_RETRYABLE":
                    await asyncio.sleep(2)
                elif exc.code != "AGENT_OUTPUT_INVALID":
                    raise
            finally:
                self.tokens.revoke(token)
        raise DomainError(
            "CANDIDATE_INVALID"
            if any(a.error_code == "CANDIDATE_INVALID" for a in attempts)
            else "AGENT_OUTPUT_INVALID"
        )

    @staticmethod
    def remaining(run):
        return max(
            0,
            (
                datetime.fromisoformat(run.deadline.replace("Z", "+00:00"))
                - datetime.fromisoformat(now().replace("Z", "+00:00"))
            ).total_seconds(),
        )

    def new_iteration(self, run):
        run.iterations.append(
            IterationDetail(
                iteration_id=uid(),
                iteration_no=len(run.iterations) + 1,
                candidate_pid=None,
                trial_status=None,
                series_ref=None,
                metrics=None,
                score=None,
                accepted=None,
                reason_code=None,
                control_output=None,
            )
        )
        self.move(run, "CANDIDATE_REQUESTED")

    def trial_request(self, run, baseline):
        iteration = None if baseline else run.iterations[-1]
        request = TrialRequest(
            schema_version="1.0",
            run_id=run.run_id,
            iteration_id=None if baseline else iteration.iteration_id,
            candidate_id=None
            if baseline
            else str(uuid5(UUID(iteration.iteration_id), "candidate")),
            control_loop_id=run.control_loop_id,
            trace_id=uid(),
            purpose="BASELINE" if baseline else "CANDIDATE",
            idempotency_key=run.run_id
            + (":" + iteration.iteration_id if iteration else ":BASELINE"),
            candidate_pid=run.actual_pid.pid if baseline else iteration.candidate_pid,
            evaluation_context_ref=run.context.context_id,
            evaluation_context_hash=run.context.context_hash,
            request_hash="",
            timeout_seconds=10,
        )
        request.request_hash = request_hash(request)
        return request

    async def trial(self, run, baseline):
        request = self.trial_request(run, baseline)
        record = await self.evaluator.submit(request)
        if (
            record.request.request_hash != request.request_hash
            or record.source_kind != run.source_kind
        ):
            raise DomainError("RESULT_INTEGRITY_ERROR")
        if baseline:
            run.baseline_trial_id = record.trial_id
        else:
            run.iterations[-1].trial_status = record.status
            run.candidate_trial_ids[run.iterations[-1].iteration_id] = record.trial_id
        if run.status.endswith("REQUESTED"):
            self.move(run, "BASELINE_RUNNING" if baseline else "TRIAL_RUNNING")
        until = asyncio.get_running_loop().time() + 40
        while record.status in ("QUEUED", "RUNNING", "UNKNOWN"):
            if asyncio.get_running_loop().time() >= until:
                raise DomainError("RESULT_UNKNOWN")
            await asyncio.sleep(0.05)
            record = await self.evaluator.get(record.trial_id)
        if record.status != "COMPLETED":
            raise DomainError(record.error.code if record.error else "RESULT_UNKNOWN")
        if record.request.evaluation_context_hash != run.context.context_hash:
            raise DomainError("RESULT_INTEGRITY_ERROR")
        points = self.series.read(record.series)
        if not quality(points, run.context.start_at).valid:
            raise DomainError("RESULT_INTEGRITY_ERROR")
        if baseline:
            run.baseline_series_ref = record.series.series_ref
            run.best_pid = run.actual_pid.pid
            run.best_metrics = metrics(points, run.context.metrics_config)
            run.best_score = score(run.best_metrics)
            self.move(run, "BASELINE_COMPLETED")
        else:
            run.iterations[-1].trial_status = "COMPLETED"
            run.iterations[-1].series_ref = record.series.series_ref
            self.move(run, "TRIAL_COMPLETED")

    async def step(self, run):
        state = run.status
        if state == "CREATED":
            run.started_at = now()
            run.deadline = (
                (
                    datetime.fromisoformat(run.started_at.replace("Z", "+00:00"))
                    + timedelta(seconds=900)
                )
                .isoformat()
                .replace("+00:00", "Z")
            )
            self.move(run, "DATA_LOADING")
        elif state == "DATA_LOADING":
            snapshot = await self.source.read_snapshot(
                SnapshotQuery(
                    schema_version="1.0",
                    control_loop_id=run.control_loop_id,
                    as_of=run.started_at,
                    window_seconds=120,
                    request_id=uid(),
                    scenario_id=run.scenario_id,
                )
            )
            if (
                snapshot.source_kind != SourceKind.EXAMPLE
                or snapshot.control_loop_id != run.control_loop_id
                or snapshot.pid.effective_at > snapshot.window_start
                or snapshot.pid.operating_condition_version != snapshot.operating_condition_version
                or snapshot.pressure_unit != "demo_unit"
                or snapshot.pid.pid_form != "DEMO_PARALLEL_PID"
                or datetime.fromisoformat(snapshot.window_end.replace("Z", "+00:00"))
                > datetime.fromisoformat(run.started_at.replace("Z", "+00:00"))
                or (
                    datetime.fromisoformat(run.started_at.replace("Z", "+00:00"))
                    - datetime.fromisoformat(snapshot.window_end.replace("Z", "+00:00"))
                ).total_seconds() > 3
            ):
                raise DomainError("SNAPSHOT_MISMATCH")
            points = self.series.read(snapshot.series)
            if not quality(points, snapshot.window_start).valid:
                raise DomainError("DATA_INVALID")
            run.snapshot, run.actual_pid = snapshot, snapshot.pid
            run.observation_series_ref = snapshot.series.series_ref
            self.move(run, "MONITOR_REQUESTED")
        elif state == "MONITOR_REQUESTED":
            run.monitor_output = await self.ask(run, "monitor")
            self.move(run, "MONITOR_COMPLETED")
        elif state == "MONITOR_COMPLETED":
            rule = assess(metrics(self.series.read(run.snapshot.series)))
            monitor = run.monitor_output
            expected_tuning = rule.assessment in ("STEADY_HIGH", "STEADY_LOW")
            if monitor.assessment != rule.assessment or monitor.needs_tuning != expected_tuning:
                raise DomainError("MONITOR_CONFLICT")
            if (
                monitor.manual_review_required
                or monitor.confidence < 0.6
                or rule.assessment == "UNCERTAIN"
            ):
                raise DomainError("MONITOR_UNCERTAIN")
            if rule.assessment == "NORMAL_STABLE":
                self.stop(run, "NO_TUNING_REQUIRED", "NORMAL_STABLE")
            elif self.monitor_only:
                # Public workflow is diagnostic only; old direct-engine runs remain
                # executable for historical recovery and adapter compatibility.
                self.stop(run, "MANUAL_REVIEW_REQUIRED", "SIMULATION_TUNING_SUGGESTED")
            else:
                run.context = EvaluationContext(
                    schema_version="1.0", context_id=uid(), snapshot_id=run.snapshot.snapshot_id,
                    scenario_id=run.scenario_id, scenario_version="examples-v2",
                    source_kind=SourceKind.EXAMPLE, adapter_id="example-evaluator",
                    adapter_version="example-v2", program_version="example-v2",
                    target_pressure=30.0, pressure_unit="demo_unit", pid_form="DEMO_PARALLEL_PID",
                    start_at=run.snapshot.window_start, duration_seconds=120.0,
                    sample_period_seconds=1.0, seed=0, metrics_config=CONFIG,
                    constraints=CONSTRAINTS, context_hash="",
                )
                run.context.context_hash = context_hash(run.context)
                self.move(run, "BASELINE_REQUESTED")
        elif state in ("BASELINE_REQUESTED", "BASELINE_RUNNING"):
            await self.trial(run, True)
        elif state == "BASELINE_COMPLETED":
            if not safe(self.series.read(self.series.resolve(run.baseline_series_ref))):
                self.stop(run, "MANUAL_REVIEW_REQUIRED", "BASELINE_UNSAFE")
            elif reached(run.best_metrics):
                self.stop(run, "SUCCEEDED", "TARGET_REACHED")
            else:
                self.new_iteration(run)
        elif state == "CANDIDATE_REQUESTED":
            output = await self.ask(run, "control")
            if output.decision == "ABSTAIN":
                run.iterations[-1].control_output = output
                self.stop(run, "MANUAL_REVIEW_REQUIRED", "CONTROL_ABSTAIN")
                return
            try:
                candidate = guard(
                    output,
                    run.best_pid,
                    [run.actual_pid.pid]
                    + [i.candidate_pid for i in run.iterations if i.candidate_pid],
                    run.context.constraints,
                )
            except DomainError as exc:
                self.pending.status, self.pending.error_code = "FAILED", "CANDIDATE_INVALID"
                self.db.save_attempt(self.pending)
                self.pending = None
                self.db.audit(run.run_id, "CANDIDATE_INVALID", {"reason_code": exc.code})
                return
            run.iterations[-1].candidate_pid = candidate
            run.iterations[-1].control_output = output
            self.move(run, "CANDIDATE_VALIDATED")
        elif state == "CANDIDATE_VALIDATED":
            self.move(run, "TRIAL_REQUESTED")
        elif state in ("TRIAL_REQUESTED", "TRIAL_RUNNING"):
            await self.trial(run, False)
        elif state == "TRIAL_COMPLETED":
            self.move(run, "EVALUATING")
        elif state == "EVALUATING":
            item = run.iterations[-1]
            points = self.series.read(self.series.resolve(item.series_ref))
            item.metrics = metrics(points, run.context.metrics_config)
            item.score = score(item.metrics)
            if not safe(points, run.context.metrics_config):
                item.accepted, item.reason_code = False, "HARD_CONSTRAINT_FAILED"
                run.status, run.stop_reason = "MANUAL_REVIEW_REQUIRED", "HARD_CONSTRAINT_FAILED"
                run.error = ErrorDetail(
                    code=run.stop_reason, message=run.stop_reason, retryable=False, trace_id=uid()
                )
                # Rejection and terminal state are a single transaction.
                with self.db.engine.begin() as conn:
                    run.finished_at = now()
                    self.db.save(
                        run,
                        run.state_version,
                        "CANDIDATE_REJECTED",
                        {"reason_code": item.reason_code},
                        conn,
                    )
                    self.db.audit(run.run_id, "MANUAL_REVIEW_REQUIRED", {}, conn)
                return
            item.accepted = (
                item.score <= run.best_score - run.context.metrics_config.min_improvement
            )
            item.reason_code = "IMPROVED" if item.accepted else "NO_IMPROVEMENT"
            if item.accepted:
                run.best_pid, run.best_metrics, run.best_score = (
                    item.candidate_pid,
                    item.metrics,
                    item.score,
                )
                run.no_improvement_count = 0
            else:
                run.no_improvement_count += 1
            self.move(run, "CANDIDATE_ACCEPTED" if item.accepted else "CANDIDATE_REJECTED")
        elif state in ("CANDIDATE_ACCEPTED", "CANDIDATE_REJECTED"):
            if reached(run.best_metrics):
                self.stop(run, "SUCCEEDED", "TARGET_REACHED")
            elif run.no_improvement_count >= 3:
                self.stop(run, "STOPPED_NO_IMPROVEMENT", "NO_IMPROVEMENT_LIMIT")
            elif len(run.iterations) >= run.max_iterations:
                self.stop(run, "STOPPED_MAX_ITERATIONS", "ITERATION_LIMIT")
            else:
                self.new_iteration(run)
