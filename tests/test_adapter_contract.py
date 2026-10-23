"""Second implementations prove the engine uses the public adapter boundary."""

from datetime import datetime, timedelta

from packages.contracts.common import ComponentHealth, SourceKind, now, uid
from packages.contracts.observations import ObservationSnapshot, PidSnapshot
from packages.contracts.trials import TrialRecord
from packages.domain.core import INITIAL, curve
from tests.test_workflow import execute


class AlternateSource:
    def __init__(self, series):
        self.series = series

    async def health(self):
        return ComponentHealth(name="alternate", ready=True, version="test-v1", error_code=None)

    async def read_snapshot(self, query):
        start = (
            (datetime.fromisoformat(query.as_of.replace("Z", "+00:00")) - timedelta(seconds=120))
            .isoformat()
            .replace("+00:00", "Z")
        )
        snapshot_id = uid()
        return ObservationSnapshot(
            schema_version="1.0",
            snapshot_id=snapshot_id,
            control_loop_id=query.control_loop_id,
            as_of=query.as_of,
            window_start=start,
            window_end=query.as_of,
            sample_period_seconds=1.0,
            source_kind=SourceKind.EXAMPLE,
            adapter_id="alternate",
            adapter_version="test-v1",
            pressure_unit="demo_unit",
            operating_condition_version="test-v1",
            pid=PidSnapshot(
                pid=INITIAL,
                pid_version="alternate-v1",
                effective_at=start,
                pid_form="DEMO_PARALLEL_PID",
                parameter_units={"kp": "demo", "ki": "demo", "kd": "demo"},
                operating_condition_version="test-v1",
            ),
            series=self.series.write(curve(INITIAL, query.scenario_id, start), snapshot_id),
        )


class AlternateEvaluator:
    def __init__(self, db, series):
        self.db, self.series = db, series
        self.records = {}
        self.keys = {}

    async def submit(self, request):
        if request.idempotency_key in self.keys:
            return self.records[self.keys[request.idempotency_key]]
        context = self.db.get(request.run_id).context
        trial_id = uid()
        record = TrialRecord(
            schema_version="1.0",
            trial_id=trial_id,
            request=request,
            status="COMPLETED",
            source_kind=SourceKind.EXAMPLE,
            adapter_id="alternate",
            adapter_version="test-v1",
            created_at=now(),
            started_at=now(),
            finished_at=now(),
            series=self.series.write(
                curve(request.candidate_pid, context.scenario_id, context.start_at), request.run_id
            ),
            error=None,
        )
        self.keys[request.idempotency_key] = trial_id
        self.records[trial_id] = record
        return record

    async def get(self, trial_id):
        return self.records[trial_id]

    async def cancel(self, trial_id):
        return self.records[trial_id]


async def test_replace_both_adapters(tmp_path):
    db, engine, evaluator, fake, run = await execute(tmp_path, steps=0)
    engine.source = AlternateSource(engine.series)
    engine.evaluator = AlternateEvaluator(db, engine.series)
    await engine.drive(run.run_id)
    done = db.get(run.run_id)
    assert done.status == "SUCCEEDED"
    assert done.snapshot.adapter_id == "alternate"
    assert len(engine.evaluator.records) == 2
    await evaluator.close()
    db.close()


async def test_example_evaluator_accepts_program_adapter(tmp_path):
    db, engine, evaluator, _, run = await execute(tmp_path, steps=0)

    class RecordingProgram:
        def __init__(self, delegate):
            self.delegate = delegate
            self.calls = 0

        async def start(self, job, inherited_fd):
            self.calls += 1
            return await self.delegate.start(job, inherited_fd)

    program = RecordingProgram(evaluator.program)
    evaluator.program = program
    try:
        await engine.drive(run.run_id)
        assert db.get(run.run_id).status == "SUCCEEDED"
        assert program.calls == 2  # baseline and candidate
    finally:
        await evaluator.close()
        db.close()
