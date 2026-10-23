"""Regressions for a safe improvement followed by premature agent abstention."""

import pytest

from apps.orchestrator.tuning import MAX_AGENT_ATTEMPTS, TuningEngine, new_run
from packages.adapters.storage import Store
from packages.contracts.tuning import ComponentAging, PlantState
from packages.domain.authorization import Tokens
from packages.domain.core import DomainError
from packages.simulation.plant import BASE_PID, PID_BOUNDS, simulate
from packages.simulation.search import search_feedback
from tests.fakes import FakeClient
from tests.test_tuning import abstain, tuning_proposal

REPORTED_STATE = PlantState(name="随机老化回路", aging=ComponentAging(
    feedwater_pump=0.15, feedwater_valve=0.21, boiler=0.35, turbine=0.37, condenser=0.57,
))


def test_reported_run_is_an_improvement_and_probes_are_untried_single_parameter_changes(tmp_path):
    db = Store(tmp_path)
    try:
        engine = TuningEngine(db, None, Tokens())
        run = new_run(REPORTED_STATE)
        db.add_tuning(run)
        engine.trial(run, BASE_PID, 0, None)
        engine.trial(run, {"kp": 2.0, "ki": 0.03, "kd": 0.5}, 1, None)
        feedback = search_feedback(run)
        progress = feedback["baseline_comparison"]
        assert progress["baseline_score"] == pytest.approx(0.032505131032012066)
        assert progress["best_score"] == pytest.approx(0.030058602284304986)
        assert 0.075 < progress["relative_reduction"] < 0.076
        assert progress["baseline_safe"] and progress["best_safe"]
        assert feedback["score_direction"] == "LOWER_IS_BETTER"
        assert feedback["evaluated_candidates"] == 1
        assert feedback["phase"] == "INITIAL_EXPLORATION"
        assert feedback["pid_independent_components"] == ["pressure", "power"]
        assert feedback["uncontrollable_pressure_violation_scenarios"] == []
        assert len(feedback["untried_single_parameter_probes"]) == 6
        for probe in feedback["untried_single_parameter_probes"]:
            changed = [key for key in PID_BOUNDS if probe["pid"][key] != run.best_pid[key]]
            assert changed == [probe["parameter"]]
            assert all(low <= probe["pid"][key] <= high for key, (low, high) in PID_BOUNDS.items())
            assert all(probe["pid"] != trial.pid for trial in run.trials)
        assert run.trials[1].accepted
        run.search_reviews = [{"iteration": 2, "reason": "证据不足" * 500}]
        run.pending_iteration = 2
        assert len(search_feedback(run)["early_abstain_review"]["reason"]) == 500
        run.pending_iteration = 3
        assert search_feedback(run)["early_abstain_review"] is None
    finally:
        db.close()


def test_physics_supports_feedback_about_pid_independent_pressure_and_power():
    weak = simulate(BASE_PID, "ramp_up", REPORTED_STATE)
    strong = simulate({"kp": 2.0, "ki": 0.03, "kd": 0.5}, "ramp_up", REPORTED_STATE)
    for field in ("steam_pressure_mpa", "power_kw"):
        assert [row[field] for row in weak] == [row[field] for row in strong]
    assert [row["feed_flow_kg_s"] for row in weak] != [row["feed_flow_kg_s"] for row in strong]


async def ready_engine(tmp_path, controls, state=None):
    db = Store(tmp_path)
    client = FakeClient(controls=controls)
    engine = TuningEngine(db, client, Tokens())
    run = new_run(state)
    db.add_tuning(run)
    for _ in range(3):
        await engine.step(db.get_tuning(run.run_id))
    return db, engine, client, run.run_id


async def test_early_abstention_is_reconsidered_once_in_same_iteration_across_restart(tmp_path):
    db, engine, client, rid = await ready_engine(tmp_path, [abstain(), tuning_proposal()])
    try:
        await engine.step(db.get_tuning(rid))
        reviewed = db.get_tuning(rid)
        assert reviewed.status == "AGENT_RUNNING" and reviewed.session_id is None
        assert len(reviewed.search_reviews) == 1 and reviewed.pending_iteration == 1
        assert len(reviewed.trials) == 1 and reviewed.no_improvement_count == 0
        assert reviewed.agent_attempt_no == 1 and reviewed.agent_failures == []
        db.close()
        db = Store(tmp_path)
        engine = TuningEngine(db, client, Tokens())
        for _ in range(3):
            await engine.step(db.get_tuning(rid))
        run = db.get_tuning(rid)
        assert len(run.trials) == 2 and run.trials[-1].iteration == 1
        assert len(run.search_reviews) == 1 and len(client.calls) == 2
        assert search_feedback(run)["early_abstain_review"] is None
        feedback = client.calls[-1][1]["control_context"]["search_feedback"]
        assert feedback["early_abstain_review"]["reason"] == abstain().reason
        assert client.calls[0][1]["request_id"] != client.calls[1][1]["request_id"]
    finally:
        db.close()


async def test_repeated_abstention_stops_without_forcing_trials_or_looping(tmp_path):
    db, engine, client, rid = await ready_engine(tmp_path, [abstain(), abstain()])
    try:
        for _ in range(3):
            await engine.step(db.get_tuning(rid))
        run = db.get_tuning(rid)
        assert run.status == "AGENT_ABSTAINED"
        assert len(run.trials) == 1 and run.no_improvement_count == 0
        assert len(client.calls) == 2 and len(run.search_reviews) == 1
        assert run.best_pid == BASE_PID
    finally:
        db.close()


async def test_uncontrollable_pressure_violation_allows_immediate_abstention(tmp_path):
    state = PlantState(aging=ComponentAging(**dict.fromkeys(ComponentAging.model_fields, 1.0)))
    db, engine, client, rid = await ready_engine(tmp_path, [abstain()], state)
    try:
        assert "ramp_up" in search_feedback(db.get_tuning(rid))["uncontrollable_pressure_violation_scenarios"]
        await engine.step(db.get_tuning(rid))
        run = db.get_tuning(rid)
        assert run.status == "NO_SAFE_CANDIDATE" and not run.search_reviews
        assert len(client.calls) == 1 and len(run.trials) == 1
    finally:
        db.close()


async def test_completed_initial_exploration_allows_abstention_without_review(tmp_path):
    db, engine, client, rid = await ready_engine(tmp_path, [abstain()])
    try:
        run = db.get_tuning(rid)
        for iteration, kp in enumerate((0.5, 0.8, 1.2), 1):
            engine.trial(run, {"kp": kp, "ki": 0.03, "kd": 0.0}, iteration, None)
        run.pending_iteration = 4
        db.save_tuning(run)
        assert search_feedback(run)["phase"] == "LOCAL_REFINEMENT"
        await engine.step(db.get_tuning(rid))
        run = db.get_tuning(rid)
        assert run.status == "AGENT_ABSTAINED" and not run.search_reviews
        assert len(client.calls) == 1
    finally:
        db.close()


async def test_review_shares_existing_attempt_budget_with_workflow_failures(tmp_path):
    controls = [DomainError("CONTROL_WORKFLOW_INVALID")] * (MAX_AGENT_ATTEMPTS - 1) + [abstain()]
    db, engine, client, rid = await ready_engine(tmp_path, controls)
    try:
        for _ in range(5):
            await engine.step(db.get_tuning(rid))
        run = db.get_tuning(rid)
        assert run.status == "AGENT_ABSTAINED" and not run.search_reviews
        assert run.agent_attempt_no == MAX_AGENT_ATTEMPTS
        assert len(client.calls) == MAX_AGENT_ATTEMPTS
        assert len(run.trials) == 1
    finally:
        db.close()


async def test_cancel_after_review_does_not_send_another_model_request(tmp_path):
    db, engine, client, rid = await ready_engine(tmp_path, [abstain()])
    try:
        await engine.step(db.get_tuning(rid))
        run = db.get_tuning(rid)
        run.cancel_requested = True
        db.save_tuning(run)
        await engine.step(db.get_tuning(rid))
        assert db.get_tuning(rid).status == "CANCELLED" and len(client.calls) == 1
    finally:
        db.close()
