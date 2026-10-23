import asyncio

import pytest
from sqlalchemy import select

from apps.orchestrator.config import Settings
from apps.orchestrator.engine import Engine, make_run
from packages.adapters.example import ExampleEvaluator, ExampleObservationSource
from packages.adapters.storage import SeriesStore, Store, events, jobs
from packages.contracts.agents import ControlOutput, MonitorOutput
from packages.contracts.common import PID
from packages.domain.authorization import Tokens
from packages.domain.core import DomainError
from tests.fakes import FakeClient, proposal


async def execute(tmp_path, scenario="improvable", fake=None, max_iterations=1, steps=None):
    settings = Settings(state_dir=tmp_path, max_iterations=max_iterations)
    db = Store(tmp_path)
    series = SeriesStore(db)
    evaluator = ExampleEvaluator(db, series)
    fake = fake or FakeClient()
    engine = Engine(
        db, series, ExampleObservationSource(series), evaluator, fake, Tokens(), settings
    )
    run = make_run(scenario, settings)
    with db.engine.begin() as conn:
        db.add(run, conn)
    if steps is None:
        await engine.drive(run.run_id)
    else:
        for _ in range(steps):
            await engine.step(db.get(run.run_id))
    return db, engine, evaluator, fake, db.get(run.run_id)


@pytest.mark.parametrize(
    "scenario,status", [("normal", "NO_TUNING_REQUIRED"), ("improvable", "SUCCEEDED")]
)
async def test_single_round(tmp_path, scenario, status):
    db, engine, evaluator, fake, run = await execute(tmp_path, scenario)
    try:
        assert run.status == status
        if scenario == "improvable":
            assert run.actual_pid.pid.kp == 1.0
            assert run.best_pid.kp == 1.1
            assert run.iterations[0].accepted is True
            assert len(fake.calls) == 2
            control_payload = next(payload for role, payload in fake.calls if role == "control")
            assert control_payload["observation_state"]["assessment"] == "STEADY_HIGH"
            assert control_payload["observation_metrics"]["signed_steady_error"] > 0.08
            assert control_payload["observation_window_end"]
            with db.engine.connect() as conn:
                assert len(conn.execute(select(jobs)).all()) == 2
    finally:
        await evaluator.close()
        db.close()


async def test_coupled_load_needs_three_bounded_trials(tmp_path):
    controls = [
        ControlOutput(
            schema_version="1.0", decision="PROPOSE", delta=PID(**delta),
            candidate_pid=PID(**candidate), reason="测试小步调整", expected_effect="降低误差",
            risk_notes=[],
        )
        for delta, candidate in [
            ({"kp": 0.1, "ki": 0.0, "kd": 0.0}, {"kp": 1.1, "ki": 0.15, "kd": 0.03}),
            ({"kp": 0.0, "ki": 0.05, "kd": 0.0}, {"kp": 1.1, "ki": 0.2, "kd": 0.03}),
            ({"kp": 0.0, "ki": 0.0, "kd": 0.02}, {"kp": 1.1, "ki": 0.2, "kd": 0.05}),
        ]
    ]
    db, _, evaluator, fake, run = await execute(
        tmp_path, "coupled_load", FakeClient(controls=controls), max_iterations=5
    )
    try:
        assert run.status == "SUCCEEDED"
        assert len(run.iterations) == 3
        assert all(item.accepted for item in run.iterations)
        assert run.iterations[0].score > run.iterations[1].score > run.iterations[2].score
        assert fake.calls[-1][1]["history"][-1]["candidate_pid"]["ki"] == 0.2
    finally:
        await evaluator.close()
        db.close()


@pytest.mark.parametrize(
    "candidate,status,reason",
    [
        (proposal(0.95, -0.05), "STOPPED_MAX_ITERATIONS", "NO_IMPROVEMENT"),
        (proposal(0.9, -0.1), "MANUAL_REVIEW_REQUIRED", "HARD_CONSTRAINT_FAILED"),
    ],
)
async def test_rejection(tmp_path, candidate, status, reason):
    db, _, evaluator, _, run = await execute(tmp_path, fake=FakeClient(controls=[candidate]))
    try:
        assert run.status == status
        assert run.best_pid.kp == 1.0
        assert run.iterations[0].accepted is False
        assert run.iterations[0].reason_code == reason
    finally:
        await evaluator.close()
        db.close()


async def test_conflict(tmp_path):
    fake = FakeClient(
        monitor=MonitorOutput(
            schema_version="1.0",
            assessment="NORMAL_STABLE",
            confidence=0.9,
            needs_tuning=False,
            manual_review_required=False,
            evidence=[],
            reason="wrong",
            warnings=[],
        )
    )
    db, _, evaluator, _, run = await execute(tmp_path, fake=fake)
    assert run.stop_reason == "MONITOR_CONFLICT"
    await evaluator.close()
    db.close()


async def test_no_improvement(tmp_path):
    fake = FakeClient(controls=[proposal(1.1, 0.1), proposal(0.95, -0.05), proposal(1.05, 0.05)])
    db, _, evaluator, _, run = await execute(tmp_path, "no_improvement", fake, 5)
    assert run.status == "STOPPED_NO_IMPROVEMENT"
    assert len(run.iterations) == 3
    await evaluator.close()
    db.close()


async def test_repair_and_candidate_budgets(tmp_path):
    fake = FakeClient(
        controls=[DomainError("AGENT_OUTPUT_INVALID"), proposal(2.0, 1.0), proposal(1.1, 0.1)]
    )
    db, _, evaluator, _, run = await execute(tmp_path, fake=fake)
    assert run.status == "SUCCEEDED"
    assert len([x for x in fake.calls if x[0] == "control"]) == 3
    await evaluator.close()
    db.close()


async def test_candidate_budget_exhausted(tmp_path):
    fake = FakeClient(controls=[proposal(2.0, 1.0), proposal(2.0, 1.0), proposal(1.1, 0.1)])
    db, _, evaluator, _, run = await execute(tmp_path, fake=fake)
    assert run.stop_reason == "CANDIDATE_INVALID"
    assert len([x for x in fake.calls if x[0] == "control"]) == 2
    await evaluator.close()
    db.close()


async def test_cancel_during_model(tmp_path):
    db, engine, evaluator, fake, run = await execute(tmp_path, steps=2)
    fake.delay = 5
    task = asyncio.create_task(engine.drive(run.run_id))
    await asyncio.sleep(0.05)
    with db.engine.begin() as conn:
        db.cancel_mark(run.run_id, conn)
    await task
    assert db.get(run.run_id).status == "CANCELLED"
    assert db.get(run.run_id).best_pid is None
    await evaluator.close()
    db.close()


async def test_recovery_after_accept(tmp_path):
    db, engine, evaluator, fake, run = await execute(tmp_path, steps=13)
    # Resume any durable point without another effective evaluation/accept.
    calls = len(fake.calls)
    await engine.drive(run.run_id)
    assert db.get(run.run_id).status == "SUCCEEDED"
    assert len(fake.calls) == calls
    with db.engine.connect() as conn:
        accepted = conn.execute(select(events).where(events.c.event == "CANDIDATE_ACCEPTED")).all()
        assert len(accepted) == 1
    await evaluator.close()
    db.close()


async def test_format_budget_exhausted(tmp_path):
    fake = FakeClient(
        controls=[DomainError("AGENT_OUTPUT_INVALID"), DomainError("AGENT_OUTPUT_INVALID")]
    )
    db, _, evaluator, _, run = await execute(tmp_path, fake=fake)
    assert run.status == "FAILED_FINAL" and run.stop_reason == "AGENT_OUTPUT_INVALID"
    assert len(fake.calls) == 3
    await evaluator.close()
    db.close()


async def test_five_round_limit_after_improvements(tmp_path):
    fake = FakeClient(controls=[proposal(round(1 + 0.002 * i, 6), 0.002) for i in range(1, 6)])
    db, _, evaluator, _, run = await execute(tmp_path, fake=fake, max_iterations=5)
    assert run.status == "STOPPED_MAX_ITERATIONS"
    assert len(run.iterations) == 5
    assert all(i.accepted for i in run.iterations)
    await evaluator.close()
    db.close()


async def test_abstain_and_low_confidence(tmp_path):
    from packages.contracts.agents import ControlOutput

    fake = FakeClient(
        controls=[
            ControlOutput(
                schema_version="1.0",
                decision="ABSTAIN",
                delta=None,
                candidate_pid=None,
                reason="no evidence",
                expected_effect="",
                risk_notes=[],
            )
        ]
    )
    db, _, evaluator, _, run = await execute(tmp_path / "abstain", fake=fake)
    assert run.stop_reason == "CONTROL_ABSTAIN"
    await evaluator.close()
    db.close()
    fake = FakeClient(
        monitor=MonitorOutput(
            schema_version="1.0",
            assessment="STEADY_HIGH",
            confidence=0.59,
            needs_tuning=True,
            manual_review_required=False,
            evidence=[],
            reason="uncertain",
            warnings=[],
        )
    )
    db, _, evaluator, _, run = await execute(tmp_path / "confidence", fake=fake)
    assert run.stop_reason == "MONITOR_UNCERTAIN"
    await evaluator.close()
    db.close()


async def test_infrastructure_retry_shares_attempt_budget(tmp_path):
    fake = FakeClient(
        controls=[
            DomainError("AGENT_INFRA_RETRYABLE"),
            DomainError("AGENT_OUTPUT_INVALID"),
            proposal(1.1, 0.1),
        ]
    )
    db, _, evaluator, _, run = await execute(tmp_path, fake=fake)
    assert run.status == "SUCCEEDED"
    assert len([x for x in fake.calls if x[0] == "control"]) == 3
    await evaluator.close()
    db.close()


async def test_model_cannot_archive_call_token(tmp_path):
    class EchoClient(FakeClient):
        async def prompt(self, session, role, payload, schema, correction=""):
            result = await super().prompt(session, role, payload, schema, correction)
            return result.model_copy(update={"reason": payload["call_context_token"]})

    db, _, evaluator, fake, run = await execute(tmp_path, fake=EchoClient())
    assert run.status == "FAILED_FINAL"
    assert run.stop_reason == "AGENT_OUTPUT_INVALID"
    from packages.adapters.storage import attempts

    with db.engine.connect() as conn:
        saved = str(conn.execute(select(attempts.c.data)).all())
    assert all(payload["call_context_token"] not in saved for _, payload in fake.calls)
    await evaluator.close()
    db.close()
