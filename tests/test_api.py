import json
import subprocess
import sys
import time

from apps.orchestrator.config import Settings
from apps.orchestrator.main import create_app as production_create_app
from apps.orchestrator.main import requests_check
from packages.adapters.example import ExampleObservationSource
from packages.domain.states import TERMINAL
from tests.auth_helpers import AuthenticatedClient as TestClient
from tests.fakes import FakeClient


def headers(client, key="test", origin="http://127.0.0.1:18000"):
    token = client.get("/api/v1/csrf-token").json()["token"]
    return {"Origin": origin, "X-CSRF-Token": token, "Idempotency-Key": key}


def create_app(*args, **kwargs):
    """Older API tests explicitly inject the direct example source."""
    kwargs.setdefault("source_factory", ExampleObservationSource)
    return production_create_app(*args, **kwargs)


def test_available_model_comes_from_running_configuration(tmp_path):
    app = create_app(
        Settings(state_dir=tmp_path, provider="local-vllm", model="qwen3.6-35b-a3b"),
        client_factory=FakeClient,
        worker_enabled=False,
    )
    with TestClient(app) as client:
        response = client.get("/api/v1/models")
        assert response.status_code == 200
        assert response.json() == {
            "schema_version": "1.0",
            "items": [{"model_id": "qwen3.6-35b-a3b", "provider": "local-vllm", "ready": True}],
        }


def test_api_full_flow(tmp_path):
    app = create_app(Settings(state_dir=tmp_path), client_factory=FakeClient)
    with TestClient(app) as client:
        body = {"schema_version": "1.0", "scenario_id": "improvable"}
        response = client.post("/api/v1/triggers", json=body, headers=headers(client))
        assert response.status_code == 202, response.text
        created = response.json()
        replay = client.post("/api/v1/triggers", json=body, headers=headers(client)).json()
        assert replay == created
        assert (
            client.post(
                "/api/v1/triggers", json={**body, "scenario_id": "normal"}, headers=headers(client)
            ).status_code
            == 409
        )
        for _ in range(200):
            run = client.get(created["status_url"]).json()
            if run["status"] in TERMINAL:
                break
            time.sleep(0.05)
        assert run["status"] == "MANUAL_REVIEW_REQUIRED", run
        assert run["stop_reason"] == "SIMULATION_TUNING_SUGGESTED"
        assert run["iterations"] == []
        assert "snapshot" not in run and "context" not in run
        curve = client.get("/api/v1/series/" + run["observation_series_ref"]).json()
        assert curve["original_sample_count"] == 121
        assert curve["source_kind"] == "EXAMPLE"
        assert client.get("/api/v1/runs").json()["items"][0]["run_id"] == run["run_id"]
        assert "call_context_token" not in json.dumps(
            client.get("/api/v1/runs/" + run["run_id"] + "/events").json()
        )
        assert client.get("/api/v1/series/..%2Fdemo.db").status_code == 404


def test_backend_and_virtual_loop_have_independent_lifecycles(tmp_path):
    from packages.adapters.live_telemetry import LiveTelemetry

    app = production_create_app(
        Settings(state_dir=tmp_path), client_factory=FakeClient, worker_enabled=False
    )
    with TestClient(app) as client:
        latest = client.get("/api/v1/telemetry/improvable/latest").json()
        assert not latest["running"] and not latest["window_ready"]
        assert not client.get("/api/v1/health").json()["ready"]
        response = client.post(
            "/api/v1/triggers",
            json={"schema_version": "1.0", "scenario_id": "improvable"},
            headers=headers(client, "loop-stopped"),
        )
        assert response.status_code == 503
        assert not (tmp_path / "telemetry" / "improvable.jsonl").exists()

    process = subprocess.Popen(
        [sys.executable, "-m", "examples.live_process", "--state-dir", str(tmp_path)],
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    live = LiveTelemetry(tmp_path)
    try:
        for _ in range(50):
            if live.is_running() and live.latest("improvable"):
                break
            if process.poll() is not None:
                raise AssertionError(process.stderr.read())
            time.sleep(0.1)
        else:
            raise AssertionError("virtual loop did not start")
        with TestClient(app) as client:
            latest = client.get("/api/v1/telemetry/improvable/latest").json()
            assert latest["running"] and not latest["window_ready"]
            assert latest["sample_kind"] == "LIVE"
        before = live.latest("improvable")["sample_second"]
        time.sleep(1.2)
        assert process.poll() is None
        assert live.latest("improvable")["sample_second"] > before
    finally:
        process.terminate()
        process.wait(timeout=5)
        process.stderr.close()
    assert not live.is_running()


def test_backend_freezes_external_loop_window_on_check(tmp_path, monkeypatch):
    from packages.adapters.live_telemetry import LiveTelemetry

    loop = LiveTelemetry(tmp_path)
    app = production_create_app(Settings(state_dir=tmp_path), client_factory=FakeClient)
    with TestClient(app) as client:
        # Build the synthetic live window after database migrations/startup,
        # so slow initialization cannot create a fictitious sampling gap.
        second = int(time.time())
        with monkeypatch.context() as seed:
            seed.setattr("packages.adapters.live_telemetry.os.fsync", lambda fd: None)
            for tick in range(second - 120, second + 1):
                loop.append_until(tick)
        client.portal.call(loop.start)
        try:
            assert client.get("/api/v1/health").json()["ready"]
            response = client.post(
                "/api/v1/triggers",
                json={"schema_version": "1.0", "scenario_id": "improvable"},
                headers=headers(client, "external-window"),
            )
            assert response.status_code == 202, response.text
            run_id = response.json()["run_id"]
            for _ in range(200):
                run = client.get("/api/v1/runs/" + run_id).json()
                if run["status"] in TERMINAL:
                    break
                time.sleep(0.05)
            assert run["status"] == "MANUAL_REVIEW_REQUIRED", run
            assert run["stop_reason"] == "SIMULATION_TUNING_SUGGESTED"
            assert run["iterations"] == []
            frozen = app.state.db.get(run_id).snapshot
            points = client.get("/api/v1/series/" + run["observation_series_ref"]).json()["points"]
            rows = loop.window("improvable", frozen.window_end)
            assert [p["pressure"] for p in points] == [row["pressure"] for row in rows]
        finally:
            client.portal.call(loop.close)


def test_reception_chat_and_check_routing(tmp_path):
    assert requests_check("请检查 PID 状态")
    assert requests_check("现在诊断压力回路")
    assert not requests_check("如何检查 PID 状态？")
    assert not requests_check("请解释 PID 的作用")
    assert not requests_check("请分析 PID 的原理")
    assert not requests_check("不要检查 PID")
    with TestClient(create_app(Settings(state_dir=tmp_path), client_factory=FakeClient)) as client:
        session = client.post(
            "/api/v1/chat/sessions",
            json={"schema_version": "1.0"},
            headers=headers(client, "session"),
        ).json()
        sid = session["session_id"]
        path = f"/api/v1/chat/sessions/{sid}/messages"
        question = {
            "schema_version": "1.0",
            "message": "如何检查 PID 状态？",
            "scenario_id": "normal",
        }
        first = client.post(path, json=question, headers=headers(client, "question"))
        assert first.status_code == 200, first.text
        assert first.json()["response"]["content"].startswith("Fake reception")
        assert first.json()["response"]["run_id"] is None
        assert (
            client.post(path, json=question, headers=headers(client, "question")).json()
            == first.json()
        )
        assert client.get("/api/v1/runs").json()["items"] == []
        check = {"schema_version": "1.0", "message": "请检查 PID 状态", "scenario_id": "normal"}
        second = client.post(path, json=check, headers=headers(client, "check"))
        assert second.status_code == 200, second.text
        run_id = second.json()["response"]["run_id"]
        assert run_id
        assert second.json()["response"]["role"] == "system"
        assert (
            client.post(path, json=check, headers=headers(client, "check")).json() == second.json()
        )
        assert client.post(path, json={**check, "scenario_id": "improvable"}, headers=headers(client, "check")).status_code == 409
        assert len(client.get(path).json()["items"]) == 4
        assert (
            client.get("/api/v1/chat/sessions").json()["items"][0]["title"] == question["message"]
        )
    with TestClient(
        create_app(Settings(state_dir=tmp_path), client_factory=FakeClient, worker_enabled=False)
    ) as client:
        assert len(client.get(path).json()["items"]) == 4
        assert client.get("/api/v1/chat/sessions").json()["items"][0]["session_id"] == sid


def test_chat_user_isolation(tmp_path):
    config = Settings(
        state_dir=tmp_path,
        local_dev=False,
        proxy_token="x" * 32,
        allowed_origin="https://demo.test",
    )
    with TestClient(create_app(config, client_factory=FakeClient, worker_enabled=False)) as client:
        client.headers.update({"X-Demo-Proxy-Token": "x" * 32, "X-Authenticated-User": "alice"})
        sid = client.post(
            "/api/v1/chat/sessions",
            json={"schema_version": "1.0"},
            headers=headers(client, "new", "https://demo.test"),
        ).json()["session_id"]
        client.app.state.platform.create_user("bob", "Bob", "regression-test-password")
        token, _, _ = client.app.state.platform.login("bob", "regression-test-password")
        client.cookies.clear()
        client.cookies.set("office_session", token)
        assert client.get(f"/api/v1/chat/sessions/{sid}/messages").status_code == 404
        assert client.patch(f"/api/v1/chat/sessions/{sid}", json={"schema_version": "1.0", "title": "别人的对话"}, headers=headers(client, "rename", "https://demo.test")).status_code == 404
        assert client.request("DELETE", f"/api/v1/chat/sessions/{sid}", json={}, headers=headers(client, "delete", "https://demo.test")).status_code == 404
        assert (
            client.post(
                f"/api/v1/chat/sessions/{sid}/messages",
                json={"schema_version": "1.0", "message": "hello"},
                headers=headers(client, "send", "https://demo.test"),
            ).status_code
            == 404
        )


def test_chat_rename_and_delete(tmp_path):
    with TestClient(create_app(Settings(state_dir=tmp_path), client_factory=FakeClient, worker_enabled=False)) as client:
        sid = client.post("/api/v1/chat/sessions", json={"schema_version": "1.0"}, headers=headers(client, "new")).json()["session_id"]
        path = f"/api/v1/chat/sessions/{sid}"
        assert client.patch(path, json={"schema_version": "1.0", "title": "  设备讨论  "}, headers=headers(client, "rename")).json()["title"] == "设备讨论"
        assert client.patch(path, json={"schema_version": "1.0", "title": "  "}, headers=headers(client, "empty")).status_code == 422
        assert client.get("/api/v1/chat/sessions").json()["items"][0]["title"] == "设备讨论"
        client.post(path + "/messages", json={"schema_version": "1.0", "message": "你好"}, headers=headers(client, "message"))
        assert client.request("DELETE", path, json={}, headers=headers(client, "delete")).status_code == 200
        assert client.get("/api/v1/chat/sessions").json()["items"] == []
        assert client.get(path + "/messages").status_code == 404
        assert client.request("DELETE", path, json={}, headers=headers(client, "delete-again")).status_code == 404


def test_auth_and_csrf(tmp_path):
    settings = Settings(
        state_dir=tmp_path,
        local_dev=False,
        proxy_token="x" * 32,
        allowed_origin="https://demo.test",
    )
    with TestClient(create_app(settings, client_factory=FakeClient)) as client:
        saved = client.cookies.get("office_session")
        client.cookies.clear()
        assert client.get("/api/v1/runs").status_code == 401
        client.headers.update({"X-Demo-Proxy-Token": "x" * 32, "X-Authenticated-User": "operator"})
        assert client.get("/api/v1/runs").status_code == 401
        client.cookies.set("office_session", saved)
        assert client.get("/api/v1/runs").status_code == 200
        body = {"schema_version": "1.0", "scenario_id": "normal"}
        assert client.post("/api/v1/triggers", json=body).status_code == 403
        assert (
            client.post(
                "/api/v1/triggers", json=body, headers=headers(client, origin="https://evil.test")
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/v1/triggers", json=body, headers=headers(client, origin="https://demo.test")
            ).status_code
            == 202
        )
        assert (
            client.post(
                "/internal/v1/tools/detect_state",
                json={"schema_version": "1.0", "call_context_token": "fake"},
            ).status_code
            == 401
        )
        invalid = client.post(
            "/api/v1/triggers",
            json={**body, "status": "SUCCEEDED"},
            headers=headers(client, "bad", "https://demo.test"),
        )
        assert invalid.status_code == 422
        assert set(invalid.json()) == {"schema_version", "error"}


def test_localhost_origin_is_allowed_only_in_local_dev(tmp_path):
    app = create_app(
        Settings(state_dir=tmp_path, allowed_origin="http://100.64.0.6:18080"),
        client_factory=FakeClient,
        worker_enabled=False,
    )
    with TestClient(app) as client:
        body = {"schema_version": "1.0", "scenario_id": "__invalid_scenario__"}
        localhost = client.post(
            "/api/v1/triggers",
            json=body,
            headers=headers(client, "localhost", "http://localhost:18000"),
        )
        assert localhost.status_code == 422
        assert localhost.json()["error"]["code"] == "SCENARIO_NOT_FOUND"
        other = client.post(
            "/api/v1/triggers",
            json=body,
            headers=headers(client, "other", "http://localhost:5173"),
        )
        assert other.status_code == 403
        assert other.json()["error"]["code"] == "CSRF_REJECTED"


def test_coalescing_and_cancel_idempotent(tmp_path):
    with TestClient(
        create_app(Settings(state_dir=tmp_path), client_factory=FakeClient, worker_enabled=False)
    ) as client:
        body = {"schema_version": "1.0", "scenario_id": "normal"}
        one = client.post("/api/v1/triggers", json=body, headers=headers(client, "one")).json()
        two = client.post("/api/v1/triggers", json=body, headers=headers(client, "two")).json()
        assert one["run_id"] == two["run_id"] and two["coalesced"]
        assert (
            client.post(
                "/api/v1/triggers",
                json={**body, "scenario_id": "improvable"},
                headers=headers(client, "three"),
            ).status_code
            == 409
        )
        url = "/api/v1/runs/" + one["run_id"] + "/cancel"
        cancel = client.post(url, json={"schema_version": "1.0"}, headers=headers(client, "cancel"))
        assert cancel.json()["cancel_requested"]
        assert cancel.json()["status"] == "CREATED"
        assert (
            client.post(
                url, json={"schema_version": "1.0"}, headers=headers(client, "cancel")
            ).json()
            == cancel.json()
        )


def test_degraded_history(tmp_path):
    with TestClient(create_app(Settings(state_dir=tmp_path))) as client:
        assert not client.get("/api/v1/health").json()["ready"]
        assert client.get("/api/v1/runs").status_code == 200
        response = client.post(
            "/api/v1/triggers",
            json={"schema_version": "1.0", "scenario_id": "normal"},
            headers=headers(client),
        )
        assert response.status_code == 503


def test_internal_tool_scope(tmp_path):
    from packages.contracts.common import uid

    settings = Settings(state_dir=tmp_path, service_token="service-secret")
    app = create_app(settings, client_factory=FakeClient, worker_enabled=False)
    with TestClient(app) as client:
        result = client.post(
            "/api/v1/triggers",
            json={"schema_version": "1.0", "scenario_id": "improvable"},
            headers=headers(client),
        ).json()
        run_id = result["run_id"]
        for _ in range(2):
            client.portal.call(app.state.engine.step, app.state.db.get(run_id))
        token = app.state.tokens.issue(run_id, uid(), None, "monitor")
        body = {"schema_version": "1.0", "call_context_token": token}
        service = {"X-MCP-Service-Token": "service-secret"}
        window = client.post(
            "/internal/v1/tools/get_recent_window", json=body, headers=service
        )
        assert window.status_code == 200
        assert len(window.json()["data"]["points"]) == 121
        assert window.json()["data"]["quality"]["valid"]
        assert (
            client.post(
                "/internal/v1/tools/get_tuning_context", json=body, headers=service
            ).status_code
            == 403
        )
        assert (
            client.post("/internal/v1/tools/apply_pid", json=body, headers=service).status_code
            == 403
        )
        assert (
            client.post(
                "/internal/v1/tools/get_recent_window",
                json={**body, "run_id": uid()},
                headers=service,
            ).status_code
            == 422
        )
        app.state.tokens.revoke(token)
        assert (
            client.post(
                "/internal/v1/tools/get_recent_window", json=body, headers=service
            ).status_code
            == 403
        )
        rows = client.get("/api/v1/runs/" + run_id + "/events").text
        assert token not in rows and "service-secret" not in rows


def test_required_schema_version(tmp_path):
    with TestClient(create_app(Settings(state_dir=tmp_path), client_factory=FakeClient)) as client:
        assert (
            client.post(
                "/api/v1/triggers", json={"scenario_id": "normal"}, headers=headers(client)
            ).status_code
            == 422
        )


def test_scheduler_deduplicates_current_slot(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from sqlalchemy import select

    import apps.orchestrator.main as module
    from packages.adapters.storage import triggers

    fixed = time.time()
    monkeypatch.setattr(
        module, "time", SimpleNamespace(time=lambda: fixed, monotonic=time.monotonic)
    )
    app = create_app(Settings(state_dir=tmp_path), client_factory=FakeClient)
    with TestClient(app) as client:
        response = client.put(
            "/api/v1/scheduler",
            json={
                "schema_version": "1.0",
                "enabled": True,
                "interval_seconds": 30,
                "scenario_id": "normal",
            },
            headers=headers(client, "schedule"),
        )
        assert response.status_code == 200
        time.sleep(2.3)
        # The scheduler needs two ticks to exercise deduplication, but fsync on
        # a busy deployment disk can delay the worker's terminal transition.
        deadline = time.monotonic() + 20
        while True:
            audit = client.get("/api/v1/runs").json()["items"]
            if audit and audit[0]["status"] in TERMINAL:
                break
            if time.monotonic() >= deadline:
                break
            time.sleep(0.1)
        with app.state.db.engine.connect() as conn:
            assert len(conn.execute(select(triggers)).all()) == 1
        assert len(audit) == 1 and audit[0]["status"] == "NO_TUNING_REQUIRED"


def test_history_cursor_has_no_duplicates(tmp_path):
    app = create_app(Settings(state_dir=tmp_path), client_factory=FakeClient, worker_enabled=False)
    from apps.orchestrator.engine import make_run

    with TestClient(app) as client:
        for _ in range(4):
            run = make_run("normal", Settings(state_dir=tmp_path))
            with app.state.db.engine.begin() as conn:
                app.state.db.add(run, conn)
                app.state.platform.claim(run.run_id, client.user_id, conn)
            app.state.engine.stop(run, "CANCELLED", "TEST")
        page = client.get("/api/v1/runs?limit=2").json()
        second = client.get(
            "/api/v1/runs", params={"limit": 2, "cursor": page["next_cursor"]}
        ).json()
        ids = [r["run_id"] for r in page["items"] + second["items"]]
        assert len(ids) == len(set(ids)) == 4
        assert second["next_cursor"] is None
        assert client.get("/api/v1/runs?cursor=invalid").status_code == 422


def test_health_reports_actual_migrated_database_revision(tmp_path):
    from sqlalchemy import text

    app = create_app(Settings(state_dir=tmp_path), client_factory=FakeClient, worker_enabled=False)
    with TestClient(app) as client:
        def database_version():
            return next(c for c in client.get("/api/v1/health").json()["components"]
                        if c["name"] == "database")["version"]

        assert database_version() == "0007"
        with app.state.db.engine.begin() as conn:
            conn.execute(text("UPDATE alembic_version SET version_num = 'test-revision'"))
        assert database_version() == "test-revision"
