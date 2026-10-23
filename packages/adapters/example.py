import asyncio
import hashlib
import json
import os
import signal
import sys
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid5

from packages.contracts.common import PID, ComponentHealth, SourceKind, now, uid
from packages.contracts.observations import ObservationSnapshot, PidSnapshot, SeriesPoint
from packages.contracts.trials import EvaluationContext, TrialRecord
from packages.domain.core import (
    COUPLED_INITIAL,
    INITIAL,
    REFERENCE,
    SCENARIOS,
    DomainError,
    context_hash,
    curve,
    quality,
    request_hash,
)

from .live_telemetry import LiveTelemetry
from .offline_simulator import JsonlProgram, LocalJsonlProgram
from .storage import SeriesStore, Store, atomic_write


class ExampleObservationSource:
    def __init__(self, series: SeriesStore, live: LiveTelemetry | None = None):
        self.series = series
        self.live = live

    async def health(self):
        ready = self.live is None or all(
            self.live.window_ready(scenario, now()) for scenario in SCENARIOS
        )
        return ComponentHealth(
            name="observation", ready=ready,
            version="example-v2", error_code=(
                None if ready else "TELEMETRY_WINDOW_NOT_READY"
            )
        )

    async def read_snapshot(self, query):
        if query.scenario_id not in SCENARIOS or query.window_seconds != 120:
            raise DomainError("SNAPSHOT_QUERY_INVALID")
        pid = (
            REFERENCE if query.scenario_id == "normal"
            else COUPLED_INITIAL if query.scenario_id == "coupled_load"
            else INITIAL
        )
        if self.live:
            records = self.live.window(query.scenario_id, query.as_of)
            current = records[-1]
            if any(
                row.get("control_loop_id") != query.control_loop_id
                or row.get("scenario_id") != query.scenario_id
                or row.get("pid") != current.get("pid")
                or row.get("pid_version") != current.get("pid_version")
                or row.get("operating_condition_version") != "examples-v2"
                or row.get("pressure_unit") != "demo_unit"
                for row in records
            ):
                raise DomainError("SNAPSHOT_MISMATCH", "观测窗口的回路或参数版本不一致")
            pid = PID.model_validate(current["pid"])
            start, end = records[0]["timestamp"], records[-1]["timestamp"]
            points = [
                SeriesPoint(
                    timestamp=row["timestamp"], elapsed_seconds=float(index),
                    pressure=row["pressure"], target_pressure=row["target_pressure"],
                    quality=row["quality"],
                )
                for index, row in enumerate(records)
            ]
        else:
            start = (
                (
                    datetime.fromisoformat(query.as_of.replace("Z", "+00:00"))
                    - timedelta(seconds=query.window_seconds)
                )
                .isoformat()
                .replace("+00:00", "Z")
            )
            end = query.as_of
            points = curve(pid, query.scenario_id, start)
        snapshot_id = uid()
        artifact = self.series.write(points, snapshot_id)
        return ObservationSnapshot(
            schema_version="1.0",
            snapshot_id=snapshot_id,
            control_loop_id=query.control_loop_id,
            as_of=end,
            window_start=start,
            window_end=end,
            sample_period_seconds=1.0,
            source_kind=SourceKind.EXAMPLE,
            adapter_id="example-observation",
            adapter_version="example-v2",
            pressure_unit="demo_unit",
            operating_condition_version="examples-v2",
            pid=PidSnapshot(
                pid=pid,
                pid_version=current["pid_version"] if self.live else "initial-v2",
                effective_at=start,
                pid_form="DEMO_PARALLEL_PID",
                parameter_units={"kp": "demo", "ki": "demo", "kd": "demo"},
                operating_condition_version="examples-v2",
            ),
            series=artifact,
        )


class ExampleEvaluator:
    def __init__(self, db: Store, series: SeriesStore, program: JsonlProgram | None = None):
        self.db, self.series = db, series
        self.program = program or LocalJsonlProgram(
            (sys.executable, "-m", "examples.program"),
            pythonpath=str(Path(__file__).parents[2]),
        )
        self.contexts: dict[str, EvaluationContext] = {}
        self.tasks: dict[str, asyncio.Task] = {}
        self.processes: dict[str, asyncio.subprocess.Process] = {}

    def register_context(self, context: EvaluationContext):
        if (
            context_hash(context) != context.context_hash
            or context.source_kind != SourceKind.EXAMPLE
        ):
            raise DomainError("RESULT_INTEGRITY_ERROR")
        self.contexts[context.context_id] = context

    async def submit(self, request):
        if request_hash(request) != request.request_hash:
            raise DomainError("RESULT_INTEGRITY_ERROR")
        context = self.contexts.get(request.evaluation_context_ref)
        if context is None:
            # Contract data is recovered from the authoritative run, not a new context.
            context = self.db.get(request.run_id).context
            if context:
                self.register_context(context)
        if (
            context is None
            or context.context_hash != request.evaluation_context_hash
            or context.context_id != request.evaluation_context_ref
        ):
            raise DomainError("RESULT_INTEGRITY_ERROR")
        if request.purpose == "CANDIDATE":
            run = self.db.get(request.run_id)
            matching = [
                i
                for i in run.iterations
                if i.iteration_id == request.iteration_id
                and i.candidate_pid == request.candidate_pid
                and request.candidate_id == str(uuid5(UUID(i.iteration_id), "candidate"))
            ]
            if len(matching) != 1 or run.status not in ("TRIAL_REQUESTED", "TRIAL_RUNNING"):
                raise DomainError("CANDIDATE_NOT_VALIDATED")
        try:
            record = self.db.job(key=request.idempotency_key)
            if record.request.request_hash != request.request_hash:
                raise DomainError("IDEMPOTENCY_CONFLICT")
        except DomainError as exc:
            if exc.code != "NOT_FOUND":
                raise
            record = TrialRecord(
                schema_version="1.0",
                trial_id=uid(),
                request=request,
                status="QUEUED",
                source_kind=SourceKind.EXAMPLE,
                adapter_id="example-evaluator",
                adapter_version="example-v2",
                created_at=now(),
                started_at=None,
                finished_at=None,
                series=None,
                error=None,
            )
            self.db.save_job(record)
        if record.status in ("QUEUED", "RUNNING") and record.trial_id not in self.tasks:
            self.tasks[record.trial_id] = asyncio.create_task(self.execute(record, context))
        return record

    async def get(self, trial_id):
        return self.db.job(trial_id=trial_id)

    async def cancel(self, trial_id):
        task = self.tasks.get(trial_id)
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        record = self.db.job(trial_id=trial_id)
        if record.status in ("QUEUED", "RUNNING"):
            record.status, record.finished_at = "CANCELLED", now()
            self.db.save_job(record)
        return record

    async def close(self):
        for trial_id in list(self.tasks):
            task = self.tasks[trial_id]
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)

    async def execute(self, record, context):
        job_dir = self.db.root / "jobs" / record.trial_id
        job_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        manifest = {
            "request": record.request.model_dump(mode="json"),
            "context": context.model_dump(mode="json"),
        }
        manifest_path = job_dir / "manifest.json"
        process = None
        try:
            if manifest_path.exists() and json.loads(manifest_path.read_text()) != manifest:
                raise DomainError("RESULT_INTEGRITY_ERROR")
            atomic_write(manifest_path, json.dumps(manifest).encode())
            record.status, record.started_at = "RUNNING", record.started_at or now()
            self.db.save_job(record)
            final = job_dir / "result.jsonl"
            # An interrupted child is never guessed dead from a PID. A held job lock
            # proves an old child may still be active and requires manual review.
            import fcntl

            lock = (job_dir / "child.lock").open("a")
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                lock.close()
                raise DomainError("RESULT_UNKNOWN") from None
            try:
                if not final.exists():
                    attempt_file = job_dir / "attempt.json"
                    count = json.loads(attempt_file.read_text()) if attempt_file.exists() else 0
                    if count >= 2:
                        raise DomainError("RESULT_UNKNOWN")
                    atomic_write(attempt_file, json.dumps(count + 1).encode())
                    process = await self.program.start(job_dir, lock.fileno())
                    self.processes[record.trial_id] = process
                    await asyncio.wait_for(process.wait(), record.request.timeout_seconds)
                    if process.returncode != 0:
                        raise DomainError("EXAMPLE_PROCESS_FAILED")
                    raw = (job_dir / "result.tmp").read_bytes()
                    points = self.validate(raw, context)
                    atomic_write(
                        job_dir / "result-receipt.json",
                        json.dumps(
                            {
                                "request_hash": record.request.request_hash,
                                "sha256": hashlib.sha256(raw).hexdigest(),
                            }
                        ).encode(),
                    )
                    os.replace(job_dir / "result.tmp", final)
                    # fsync final and directory before publishing DB reference.
                    atomic_write(final, raw)
                else:
                    raw = final.read_bytes()
                    receipt_path = job_dir / "result-receipt.json"
                    if not receipt_path.exists():
                        raise DomainError("RESULT_INTEGRITY_ERROR")
                    receipt = json.loads(receipt_path.read_text())
                    if receipt != {
                        "request_hash": record.request.request_hash,
                        "sha256": hashlib.sha256(raw).hexdigest(),
                    }:
                        raise DomainError("RESULT_INTEGRITY_ERROR")
                    points = self.validate(raw, context)
                record.series = self.series.write(
                    points, record.request.run_id, ref=record.trial_id
                )
                record.status, record.finished_at = "COMPLETED", now()
                self.db.save_job(record)
            finally:
                lock.close()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            from packages.contracts.common import ErrorDetail

            record.status, record.finished_at = "FAILED", now()
            record.series = None
            record.error = ErrorDetail(
                code=exc.code if isinstance(exc, DomainError) else "EXAMPLE_PROCESS_FAILED",
                message="示例评测失败，详见错误码",
                retryable=False,
                trace_id=record.request.trace_id,
            )
            self.db.save_job(record)
        finally:
            if process and process.returncode is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    await asyncio.wait_for(process.wait(), 2)
                except asyncio.TimeoutError:
                    os.killpg(process.pid, signal.SIGKILL)
                    await process.wait()
            self.processes.pop(record.trial_id, None)

    @staticmethod
    def validate(raw, context):
        try:
            if len(raw) > 2_000_000:
                raise ValueError("oversize")
            points = [SeriesPoint.model_validate_json(x) for x in raw.splitlines()]
            if not quality(
                points,
                context.start_at,
                context.duration_seconds,
                context.sample_period_seconds,
                context.target_pressure,
            ).valid:
                raise ValueError("quality")
            return points
        except ValueError as exc:
            raise DomainError("RESULT_INTEGRITY_ERROR") from exc
