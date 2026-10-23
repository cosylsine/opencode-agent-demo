"""Wear changes plant physics while every trial retains its run's frozen state."""

import json
import time
from math import isfinite

import pytest
from pydantic import ValidationError

from apps.orchestrator.config import Settings
from apps.orchestrator.main import create_app
from apps.orchestrator.tuning import TuningEngine, new_run
from packages.adapters.storage import Store
from packages.contracts.tuning import ComponentAging, PlantState, TuningRun, TuningStart
from packages.domain.authorization import Tokens
from packages.domain.core import DomainError
from packages.simulation.evaluate import evaluate_pid
from packages.simulation.plant import (
    BASE_PID,
    SCENARIOS,
    model_parameters,
    plant_state_hash,
    simulate,
)
from tests.auth_helpers import AuthenticatedClient as TestClient
from tests.fakes import FakeClient
from tests.test_api import headers
from tests.test_tuning import abstain, tuning_proposal


def worn_state(value=0.5):
    return PlantState(name="检修前回路", aging=ComponentAging(
        **dict.fromkeys(ComponentAging.model_fields, value),
    ))


@pytest.mark.parametrize("component", ComponentAging.model_fields)
def test_each_component_changes_repeatable_mass_conserving_dynamics(component):
    state = PlantState(aging=ComponentAging(**{component: 0.7}))
    nominal, _ = evaluate_pid(BASE_PID)
    result, traces = evaluate_pid(BASE_PID, state)
    assert result["score"] != pytest.approx(nominal["score"], abs=1e-6)
    assert (result, traces) == evaluate_pid(BASE_PID, state)
    assert set(traces) == set(SCENARIOS)
    for rows in traces.values():
        assert len(rows) == 421
        assert all(isfinite(value) for row in rows for value in row.values())
        assert max(abs(row["drum_water_kg"] + row["drum_steam_kg"]
                       + row["condenser_water_kg"] - 2830.0) for row in rows) < 1e-8


def test_zero_aging_retains_nominal_response_and_full_wear_does_not_relax_safety():
    result, traces = evaluate_pid(BASE_PID)
    assert (result, traces) == evaluate_pid(BASE_PID, worn_state(0.0))
    assert result["safe"]
    assert traces["step_up"][0]["power_kw"] == pytest.approx(500.0)
    assert traces["step_up"][60]["valve_open_fraction"] == pytest.approx(0.77)
    result, traces = evaluate_pid(BASE_PID, worn_state(1.0))
    assert not result["safe"]
    assert "STEAM_PRESSURE" in result["scenarios"]["ramp_up"]["violations"]
    assert all(row["heat_input_kw"] <= 3200 for rows in traces.values() for row in rows)


def test_aging_parameters_have_expected_direction_and_valve_lags():
    nominal, worn = model_parameters(PlantState()), model_parameters(worn_state(1.0))
    for key in ("pump_head_factor", "valve_capacity_factor", "boiler_efficiency_factor",
                "turbine_work_factor", "condenser_work_factor"):
        assert 0 < worn[key] < nominal[key]
    for key in ("pump_speed_lag_s", "pump_flow_lag_s", "valve_lag_s",
                "boiler_heat_lag_s", "turbine_flow_lag_s", "condenser_pressure_mpa"):
        assert worn[key] > nominal[key]
    rows = simulate(BASE_PID, "step_up", PlantState(aging=ComponentAging(feedwater_valve=1.0)))
    assert 0.7 < rows[60]["valve_open_fraction"] < 0.77


@pytest.mark.parametrize("value", [-0.01, 1.01, True, "0.5", float("nan"), float("inf"), None])
@pytest.mark.parametrize("component", ComponentAging.model_fields)
def test_invalid_aging_is_rejected(component, value):
    with pytest.raises(ValidationError):
        TuningStart.model_validate({"schema_version": "1.0", "plant_state": {
            "aging": {component: value},
        }})


def test_state_is_immutable_and_renaming_does_not_change_physical_identity():
    state = worn_state()
    assert plant_state_hash(state) == plant_state_hash(state.model_copy(update={"name": "改名"}))
    assert plant_state_hash(state) != plant_state_hash(PlantState())
    with pytest.raises(ValidationError):
        state.aging.boiler = 0.1
    with pytest.raises(ValidationError):
        state.name = "运行中不能修改"


def test_old_run_without_aging_remains_readable():
    data = new_run().model_dump(mode="json")
    data.pop("plant_state")
    data.pop("plant_state_hash")
    restored = TuningRun.model_validate_json(json.dumps(data))
    assert restored.plant_state.aging == ComponentAging()
    assert restored.plant_state_hash == ""


def test_engine_rejects_a_changed_state_before_evaluating_any_candidate(tmp_path):
    store = Store(tmp_path)
    try:
        run = new_run(worn_state())
        store.add_tuning(run)
        run.plant_state = worn_state(0.2)
        with pytest.raises(DomainError, match="回路状态与本次调优固定的状态不一致"):
            TuningEngine(store, None, Tokens()).trial(run, BASE_PID, 0, None)
        assert store.get_tuning(run.run_id).trials == []
    finally:
        store.close()


def test_engine_restart_keeps_aged_state_and_compares_candidates_with_same_baseline(tmp_path):
    state = worn_state()
    store = Store(tmp_path)
    run = new_run(state)
    try:
        store.add_tuning(run)
        TuningEngine(store, None, Tokens()).trial(run, BASE_PID, 0, None)
        run_id = run.run_id
    finally:
        store.close()
    store = Store(tmp_path)
    try:
        engine = TuningEngine(store, None, Tokens())
        run = store.get_tuning(run_id)
        engine.trial(run, tuning_proposal().candidate_pid.root, 1, tuning_proposal())
        assert run.plant_state == state
        assert len(run.trials) == 2 and run.trials[1].accepted
        for trial in run.trials:
            assert trial.result == evaluate_pid(trial.pid, state)[0]
            assert trial.result["plant_state_hash"] == run.plant_state_hash
        context = engine.payload(run, "request", "token")
        assert context["plant_state"] == state.model_dump(mode="json")
        assert context["plant_state_hash"] == run.plant_state_hash
        assert context["effective_parameters"] == model_parameters(state)
    finally:
        store.close()


def test_api_freezes_state_for_baseline_candidates_context_and_history(tmp_path):
    state = worn_state()
    fake = FakeClient(controls=[tuning_proposal(), abstain()])
    app = create_app(Settings(state_dir=tmp_path), client_factory=lambda _: fake)
    with TestClient(app) as client:
        body = {"schema_version": "1.0", "plant_state": state.model_dump(mode="json")}
        response = client.post("/api/v1/tuning-runs", json=body, headers=headers(client, "aged"))
        assert response.status_code == 202, response.text
        run_id = response.json()["run_id"]
        assert response.json()["plant_state"] == body["plant_state"]
        replay = client.post("/api/v1/tuning-runs", json=body, headers=headers(client, "aged"))
        assert replay.json()["run_id"] == run_id
        conflict = client.post("/api/v1/tuning-runs", json={**body, "plant_state": {
            "aging": {"boiler": 0.1},
        }}, headers=headers(client, "aged"))
        assert conflict.status_code == 409
        for _ in range(200):
            run = client.get("/api/v1/tuning-runs/" + run_id).json()
            if run["status"] in {"AGENT_ABSTAINED", "FAILED", "NO_SAFE_CANDIDATE"}:
                break
            time.sleep(0.05)
        assert run["status"] == "AGENT_ABSTAINED", run
        assert len(run["trials"]) == 2
        assert run["trials"][1]["accepted"]
        assert run["plant_state"] == body["plant_state"]
        assert all(t["result"]["plant_state_hash"] == run["plant_state_hash"] for t in run["trials"])
        control = next(payload for role, payload in fake.calls if role == "control")
        assert control["control_context"]["plant_state"] == body["plant_state"]
        assert control["control_context"]["effective_parameters"] == model_parameters(state)
        trial_id = run["trials"][0]["trial_id"]
        points = client.get(f"/api/v1/tuning-runs/{run_id}/trials/{trial_id}/scenarios/step_up/trace").json()["points"]
        assert points == simulate(BASE_PID, "step_up", state)
    with TestClient(create_app(Settings(state_dir=tmp_path), client_factory=FakeClient,
                               worker_enabled=False)) as client:
        saved = client.get("/api/v1/tuning-runs/" + run_id).json()
        assert saved["plant_state"] == body["plant_state"]
        assert saved["plant_state_hash"] == plant_state_hash(state)


@pytest.mark.parametrize("state", [
    {"aging": {"boiler": 1.1}}, {"aging": {"pump": 0.5}},
    {"aging": {"boiler": True}}, {"aging": {"boiler": "0.5"}},
    {"name": ""}, {"name": "   "}, {"name": "a" * 81}, {"aging": None},
    {"aging": {"boiler": 0.2}, "extra": "not allowed"},
])
def test_api_rejects_invalid_state_without_creating_run(tmp_path, state):
    app = create_app(Settings(state_dir=tmp_path), client_factory=FakeClient, worker_enabled=False)
    with TestClient(app) as client:
        response = client.post("/api/v1/tuning-runs", json={
            "schema_version": "1.0", "plant_state": state,
        }, headers=headers(client, "invalid-state"))
        assert response.status_code == 422, response.text
        assert client.get("/api/v1/tuning-runs").json()["items"] == []
