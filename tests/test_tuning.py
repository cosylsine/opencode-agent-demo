import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from apps.orchestrator.config import Settings
from apps.orchestrator.main import create_app
from apps.orchestrator.tuning import TuningEngine, new_run, validate_candidate
from packages.adapters.storage import Store
from packages.contracts.agents import ControlOutput
from packages.contracts.common import PID, uid
from packages.domain.authorization import Tokens
from packages.domain.core import DomainError
from packages.simulation.evaluate import evaluate_pid, evaluate_trace
from packages.simulation.plant import BASE_PID, NOMINAL_FLOW, SCENARIOS, simulate
from tests.auth_helpers import AuthenticatedClient as TestClient
from tests.fakes import FakeClient
from tests.test_api import headers


def tuning_proposal():
    return ControlOutput(schema_version="1.0", decision="PROPOSE",
                         delta=PID(kp=0.6, ki=0.055, kd=0.0),
                         candidate_pid=PID(kp=0.8, ki=0.06, kd=0.0),
                         reason="改善给水流量跟踪", expected_effect="减少负荷变化后的偏差", risk_notes=[])


def abstain():
    return ControlOutput(schema_version="1.0", decision="ABSTAIN", delta=None,
                         candidate_pid=None, reason="当前没有更可靠的候选", expected_effect="维持最佳参数", risk_notes=[])


def test_plant_mass_balance_and_load_response():
    for name in SCENARIOS:
        rows = simulate(BASE_PID, name)
        assert len(rows) == 421
        assert abs(rows[0]["feed_flow_kg_s"] - NOMINAL_FLOW) < 1e-12
        assert abs(rows[0]["power_kw"] - 500) < 1e-12
        assert max(abs(r["drum_water_kg"] + r["drum_steam_kg"] + r["condenser_water_kg"] - 2830) for r in rows) < 1e-8
    up, down = simulate(BASE_PID, "step_up"), simulate(BASE_PID, "step_down")
    assert up[100]["steam_flow_kg_s"] > NOMINAL_FLOW > down[100]["steam_flow_kg_s"]
    assert up[100]["feed_flow_kg_s"] > NOMINAL_FLOW > down[100]["feed_flow_kg_s"]
    assert up[100]["steam_pressure_mpa"] < 3.8 < down[100]["steam_pressure_mpa"]


def test_same_pid_all_scenarios_and_safety_first():
    base, curves = evaluate_pid(BASE_PID)
    better, _ = evaluate_pid({"kp": 0.8, "ki": 0.06, "kd": 0.0})
    assert base["safe"] and better["safe"] and better["score"] < base["score"]
    assert set(curves) == set(SCENARIOS)
    assert evaluate_pid(BASE_PID)[0] == base
    damaged = [row.copy() for row in curves["step_up"]]
    damaged[100]["drum_level_fraction"] = 0.1
    assert evaluate_trace(damaged)["violations"] == ["DRUM_LEVEL"]
    try:
        validate_candidate(tuning_proposal(), BASE_PID, [{"kp": 0.8, "ki": 0.06, "kd": 0.0}])
    except DomainError as exc:
        assert exc.code == "CANDIDATE_INVALID"
    else:
        raise AssertionError("duplicate candidate was accepted")


def test_api_tuning_is_independent_and_persisted(tmp_path):
    def client_factory(config):
        return FakeClient(controls=[tuning_proposal(), abstain()])

    app = create_app(Settings(state_dir=tmp_path), client_factory=client_factory)
    with TestClient(app) as client:
        created = client.post('/api/v1/tuning-runs', json={'schema_version':'1.0'}, headers=headers(client, 'tune'))
        assert created.status_code == 202, created.text
        run_id = created.json()['run_id']
        replay = client.post('/api/v1/tuning-runs', json={'schema_version':'1.0'}, headers=headers(client, 'tune'))
        assert replay.json()['run_id'] == run_id
        for _ in range(200):
            run = client.get('/api/v1/tuning-runs/' + run_id).json()
            if run['status'] in ('COMPLETED','AGENT_ABSTAINED','FAILED'):
                break
            time.sleep(0.05)
        assert run['status'] == 'AGENT_ABSTAINED', run
        assert len(run['trials']) == 2
        assert run['trials'][1]['accepted']
        assert run['best_pid'] == {'kp':0.8,'ki':0.06,'kd':0.0}
        assert run['source_kind'] == 'SIMULATION'
        trace = client.get(f'/api/v1/tuning-runs/{run_id}/trials/{run["best_trial_id"]}/scenarios/step_up/trace').json()
        assert trace['source_kind'] == 'SIMULATION'
        assert len(trace['points']) == 421
        assert trace['points'][60]['target_power_kw'] == 550
        assert client.get('/api/v1/runs').json()['items'] == []
        assert client.get('/api/v1/tuning-runs').json()['items'][0]['run_id'] == run_id
    with TestClient(create_app(Settings(state_dir=tmp_path), client_factory=client_factory, worker_enabled=False)) as client:
        assert client.get('/api/v1/tuning-runs/' + run_id).json()['best_pid'] == run['best_pid']


def test_invalid_agent_candidate_is_recorded_then_search_continues(tmp_path):
    wrong_delta = ControlOutput(schema_version="1.0", decision="PROPOSE",
                                delta=PID(kp=0, ki=0, kd=0),
                                candidate_pid=PID(kp=0.8, ki=0.06, kd=0),
                                reason="故意不一致的 delta", expected_effect="测试校验", risk_notes=[])
    app = create_app(Settings(state_dir=tmp_path),
                     client_factory=lambda config: FakeClient(controls=[wrong_delta, tuning_proposal(), abstain()]))
    with TestClient(app) as client:
        created = client.post('/api/v1/tuning-runs', json={'schema_version':'1.0'}, headers=headers(client, 'reject'))
        assert created.status_code == 202
        run_id = created.json()['run_id']
        for _ in range(200):
            run = client.get('/api/v1/tuning-runs/' + run_id).json()
            if run['status'] == 'AGENT_ABSTAINED':
                break
            time.sleep(0.05)
        assert run['status'] == 'AGENT_ABSTAINED', run
        assert len(run['rejections']) == 1
        assert run['rejections'][0]['iteration'] == 1
        assert 'delta' in run['rejections'][0]['reason']
        assert run['trials'][1]['iteration'] == 2 and run['trials'][1]['accepted']


def test_cancel_while_agent_is_running(tmp_path):
    fake = FakeClient(controls=[tuning_proposal()], delay=1)
    app = create_app(Settings(state_dir=tmp_path), client_factory=lambda config: fake)
    with TestClient(app) as client:
        created = client.post('/api/v1/tuning-runs', json={'schema_version':'1.0'}, headers=headers(client, 'cancel-start'))
        assert created.status_code == 202
        run_id = created.json()['run_id']
        for _ in range(100):
            run = client.get('/api/v1/tuning-runs/' + run_id).json()
            if run['status'] == 'AGENT_RUNNING' and fake.calls:
                break
            time.sleep(0.02)
        assert run['status'] == 'AGENT_RUNNING'
        cancelled = client.post(f'/api/v1/tuning-runs/{run_id}/cancel',
                                json={'schema_version':'1.0'}, headers=headers(client, 'cancel-now'))
        assert cancelled.status_code == 202
        for _ in range(100):
            run = client.get('/api/v1/tuning-runs/' + run_id).json()
            if run['status'] == 'CANCELLED':
                break
            time.sleep(0.03)
        assert run['status'] == 'CANCELLED'
        assert len(run['trials']) == 1


async def test_restart_and_cancel_at_durable_boundary(tmp_path):
    db = Store(tmp_path)
    run = new_run()
    db.add_tuning(run)
    client = FakeClient(controls=[tuning_proposal()])
    engine = TuningEngine(db, client, Tokens())
    await engine.step(db.get_tuning(run.run_id))
    await engine.step(db.get_tuning(run.run_id))
    assert len(db.get_tuning(run.run_id).trials) == 1
    db.close()
    db = Store(tmp_path)
    resumed = TuningEngine(db, client, Tokens())
    await resumed.step(db.get_tuning(run.run_id))
    await resumed.step(db.get_tuning(run.run_id))
    await resumed.step(db.get_tuning(run.run_id))
    await resumed.step(db.get_tuning(run.run_id))
    value = db.get_tuning(run.run_id)
    assert len(value.trials) == 2 and value.best_trial_id == value.trials[1].trial_id
    value.cancel_requested = True
    db.save_tuning(value)
    await resumed.step(db.get_tuning(run.run_id))
    assert db.get_tuning(run.run_id).status == 'CANCELLED'
    db.close()


async def test_agent_abstain_without_safe_candidate_reports_failure(tmp_path):
    db = Store(tmp_path)
    run = new_run()
    run.status = 'AGENT_RUNNING'
    run.agent_sent = True
    run.pending_iteration = 1
    run.session_id = 'ses_test'
    db.add_tuning(run)
    fake = FakeClient()
    fake.sessions['ses_test'] = abstain()
    engine = TuningEngine(db, fake, Tokens())
    await engine.step(db.get_tuning(run.run_id))
    result = db.get_tuning(run.run_id)
    assert result.status == 'NO_SAFE_CANDIDATE'
    assert result.best_pid is None
    assert '没有合规候选' in result.error
    db.close()


def test_tuning_context_keeps_baseline_all_trials_and_rejections(tmp_path):
    db = Store(tmp_path)
    run = new_run()
    engine = TuningEngine(db, FakeClient(), Tokens())
    db.add_tuning(run)
    engine.trial(run, run.baseline_pid, 0, None)
    baseline = run.trials[0]
    # A completed run contains a baseline plus up to twelve candidate trials.
    run.trials.extend(baseline.model_copy(update={"iteration": i, "trial_id": uid()})
                      for i in range(1, 13))
    run.rejections = [{"iteration": 2, "reason": "参数边界或 delta 不一致"}]
    payload = engine.payload(run, uid(), "test-token")
    assert payload["actual_pid"] == payload["baseline_pid"] == run.baseline_pid
    assert payload["actual_pid_source"] == "SIMULATION_BASELINE"
    assert [item["iteration"] for item in payload["history"]] == list(range(13))
    assert payload["rejections"] == run.rejections
    assert 0 < payload["remaining_seconds"] <= 1200
    assert payload["deadline_at"] == run.deadline_at
    db.close()


def test_internal_tuning_context_is_bound_to_live_request(tmp_path):
    settings = Settings(state_dir=tmp_path, service_token="service-secret")
    app = create_app(settings, client_factory=FakeClient, worker_enabled=False)
    with TestClient(app) as client:
        engine = app.state.tuning
        run = new_run()
        app.state.db.add_tuning(run)
        for _ in range(3):
            client.portal.call(engine.step, app.state.db.get_tuning(run.run_id))
        run = app.state.db.get_tuning(run.run_id)
        run.agent_sent = True
        app.state.db.save_tuning(run)
        token = app.state.tokens.issue(run.run_id, run.request_id,
                                       str(run.pending_iteration), "control")
        body = {"schema_version": "1.0", "call_context_token": token}
        service = {"X-MCP-Service-Token": "service-secret"}
        url = "/internal/v1/tools/get_tuning_context"
        assert client.post(url, json=body).status_code == 401
        response = client.post(url, json=body, headers=service)
        assert response.status_code == 200, response.text
        context = response.json()["data"]
        assert context["actual_pid"] == run.baseline_pid
        assert context["current_best"]["pid"] == run.best_pid
        assert len(context["history"]) == 1
        assert "call_context_token" not in context
        # A token from another request/iteration cannot read this run.
        for request_id, iteration in [(uid(), str(run.pending_iteration)), (run.request_id, "2")]:
            stale = app.state.tokens.issue(run.run_id, request_id, iteration, "control")
            assert client.post(url, json={**body, "call_context_token": stale},
                               headers=service).status_code == 403
        run = app.state.db.get_tuning(run.run_id)
        run.deadline_at = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        app.state.db.save_tuning(run)
        assert client.post(url, json=body, headers=service).status_code == 403


async def test_tuning_token_covers_program_context_read_and_is_revoked(tmp_path, monkeypatch):
    from packages.domain import authorization

    clock = [1000.0]
    monkeypatch.setattr(authorization, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    tokens = Tokens()
    used = []

    class SlowClient(FakeClient):
        async def control_context(self, token, workflow):
            used.append(token)
            clock[0] += 310  # Beyond the old 300 s TTL, within result recovery.
            tokens.authorize(token, "get_tuning_context")
            return await super().control_context(token, workflow)

        async def prompt(self, session, role, payload, schema, correction="", *, workflow=None):
            assert "call_context_token" not in payload
            return abstain()

    db = Store(tmp_path)
    engine = TuningEngine(db, SlowClient(), tokens)
    run = new_run()
    db.add_tuning(run)
    for _ in range(4):
        await engine.step(db.get_tuning(run.run_id))
    assert used
    with pytest.raises(DomainError, match="TOOL_FORBIDDEN"):
        tokens.authorize(used[0], "get_tuning_context")
    db.close()
