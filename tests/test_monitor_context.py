import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace

import httpx
import pytest

from apps.orchestrator.config import Settings
from packages.adapters.opencode import MONITOR_READ_TOOLS, MONITOR_SKILLS, OpenCodeClient
from packages.contracts.agents import MonitorOutput
from packages.contracts.common import uid
from packages.domain.core import DomainError
from tests.auth_helpers import AuthenticatedClient
from tests.fakes import FakeClient
from tests.test_api import create_app, headers
from tests.test_monitor_transport import structured_message


@pytest.fixture
def monitor_backend(tmp_path, monkeypatch):
    """Mock only MCP transport; authorization, frozen data and audit are real."""
    import mcp
    import mcp.client.stdio

    settings = Settings(state_dir=tmp_path, service_token="monitor-test-service-secret")
    app = create_app(settings, client_factory=FakeClient, worker_enabled=False)
    with AuthenticatedClient(app) as backend:
        result = backend.post(
            "/api/v1/triggers", json={"schema_version": "1.0", "scenario_id": "normal"},
            headers=headers(backend),
        )
        assert result.status_code == 202
        run_id = result.json()["run_id"]
        for _ in range(2):
            backend.portal.call(app.state.engine.step, app.state.db.get(run_id))
        request_id = uid()
        token = app.state.tokens.issue(run_id, request_id, None, "monitor")
        payload = app.state.engine.agent_payload(
            app.state.db.get(run_id), "monitor", request_id, token
        ).model_dump(mode="json")
        state = SimpleNamespace(
            app=app, backend=backend, settings=settings, payload=payload,
            calls=[], results={}, closed=False, alter=None, is_error=False, text_only=False,
            service_token=settings.service_token, block=False, entered=asyncio.Event(),
        )

        @asynccontextmanager
        async def stdio(params):
            assert params.args == ["-m", "apps.mcp_server"]
            assert params.env["MCP_SERVICE_TOKEN"] == settings.service_token
            try:
                yield None, None
            finally:
                state.closed = True

        class Session:
            def __init__(self, *_):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                pass

            async def initialize(self):
                pass

            async def call_tool(self, name, arguments):
                state.calls.append(name)
                state.entered.set()
                if state.block:
                    await asyncio.Event().wait()
                response = backend.post(
                    "/internal/v1/tools/" + name,
                    json={"schema_version": "1.0", **arguments},
                    headers={"X-MCP-Service-Token": state.service_token},
                )
                value = response.json()
                state.results[name] = value
                if state.alter:
                    state.alter(name, value)
                return SimpleNamespace(
                    isError=state.is_error,
                    structuredContent=None if state.text_only else value,
                    content=[SimpleNamespace(type="text", text=json.dumps(value))],
                )

        monkeypatch.setattr(mcp.client.stdio, "stdio_client", stdio)
        monkeypatch.setattr(mcp, "ClientSession", Session)
        yield state


def audit_reads(state):
    events = state.backend.get("/api/v1/runs/" + state.payload["run_id"] + "/events").json()
    return [item["detail"] for item in events["items"] if item["event"] == "MCP_READ"]


@pytest.mark.parametrize("text_only", [False, True])
async def test_monitor_prefetch_authorized_reads_audited_before_model(monitor_backend, text_only):
    state = monitor_backend
    state.text_only = text_only
    original = json.dumps(state.payload, sort_keys=True)
    client = OpenCodeClient(state.settings)

    def model(request):
        assert state.closed
        assert state.calls == list(MONITOR_READ_TOOLS)
        assert audit_reads(state) == [
            {"request_id": state.payload["request_id"], "tool": name, "role": "monitor"}
            for name in MONITOR_READ_TOOLS
        ]
        body = json.loads(request.content)
        prepared = json.loads(body["parts"][0]["text"].split("\n校验反馈：")[0])
        assert "call_context_token" not in prepared
        assert state.payload["call_context_token"] not in request.content.decode()
        assert state.settings.service_token not in request.content.decode()
        assert prepared["monitor_context"] == {name: state.results[name]["data"] for name in MONITOR_READ_TOOLS}
        assert [item["name"] for item in prepared["skills_preloaded"]] == list(MONITOR_SKILLS)
        for name in MONITOR_READ_TOOLS:
            assert body["tools"]["industrial_control_" + name] is False
        assert body["tools"]["skill"] is False
        for name in MONITOR_SKILLS:
            assert "<preloaded_skill name=" + name in body["system"]
        return httpx.Response(200, json=structured_message())

    await client.http.aclose()
    client.http = httpx.AsyncClient(base_url="http://localhost", transport=httpx.MockTransport(model))
    try:
        output = await client.prompt("ses_monitor_transport", "monitor", state.payload, MonitorOutput)
        assert output.assessment == "NORMAL_STABLE"
        assert json.dumps(state.payload, sort_keys=True) == original
    finally:
        await client.close()


@pytest.mark.parametrize("failure", [
    "revoked", "wrong_role", "service", "tool_error", "missing_data", "snapshot", "pid",
    "quality", "metrics", "rule", "points", "credential", "pid_bool",
])
async def test_monitor_read_failure_never_calls_model(monitor_backend, failure):
    state = monitor_backend
    if failure == "revoked":
        state.app.state.tokens.revoke(state.payload["call_context_token"])
    elif failure == "wrong_role":
        state.payload["call_context_token"] = state.app.state.tokens.issue(
            state.payload["run_id"], uid(), None, "control"
        )
    elif failure == "service":
        state.service_token = "wrong-service-secret"
    elif failure == "tool_error":
        state.is_error = True
    else:
        def alter(name, value):
            data = value["data"]
            if failure == "missing_data" and name == "detect_state":
                value.pop("data")
            elif failure == "snapshot" and name == "get_recent_window":
                data["snapshot_id"] = uid()
            elif failure == "pid" and name == "get_recent_window":
                data["actual_pid"]["pid"]["kp"] += 1
            elif failure == "pid_bool" and name == "get_recent_window":
                data["actual_pid"]["pid"]["kp"] = True
            elif failure == "quality" and name == "get_recent_window":
                data["quality"]["valid"] = False
            elif failure == "metrics" and name == "calculate_metrics":
                data["steady_state_error"] += 1
            elif failure == "rule" and name == "detect_state":
                data["assessment"] = "STEADY_HIGH"
            elif failure == "points" and name == "get_recent_window":
                data["points"] = []
            elif failure == "credential" and name == "get_recent_window":
                data["actual_pid"]["pid_version"] = state.payload["call_context_token"]
        state.alter = alter
    client = OpenCodeClient(state.settings)

    def model(_):
        pytest.fail("failed preparation must never contact the model")

    await client.http.aclose()
    client.http = httpx.AsyncClient(base_url="http://localhost", transport=httpx.MockTransport(model))
    try:
        with pytest.raises(DomainError) as caught:
            await client.prompt("ses_monitor_transport", "monitor", state.payload, MonitorOutput)
        assert caught.value.code == "MONITOR_CONTEXT_UNAVAILABLE"
        assert state.payload["call_context_token"] not in str(caught.value)
        assert state.settings.service_token not in str(caught.value)
        assert state.closed
        if failure in {"revoked", "wrong_role", "service"}:
            assert audit_reads(state) == []
    finally:
        await client.close()


async def test_monitor_cancel_cleans_mcp_without_calling_model(monitor_backend):
    state = monitor_backend
    state.block = True
    client = OpenCodeClient(state.settings)
    try:
        task = asyncio.create_task(client.prepare_monitor(state.payload))
        await asyncio.wait_for(state.entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert state.closed
        assert audit_reads(state) == []
    finally:
        await client.close()


async def test_monitor_read_deadline_closes_mcp_without_calling_model(monitor_backend, monkeypatch):
    from packages.adapters import opencode

    state = monitor_backend
    state.block = True
    monkeypatch.setattr(opencode, "MONITOR_CONTEXT_TIMEOUT_SECONDS", 0.01)
    client = OpenCodeClient(state.settings)
    try:
        with pytest.raises(DomainError) as caught:
            await asyncio.wait_for(client.prepare_monitor(state.payload), timeout=1)
        assert caught.value.code == "MONITOR_CONTEXT_UNAVAILABLE"
        assert state.closed
        assert audit_reads(state) == []
    finally:
        await client.close()


async def test_monitor_missing_skills_prevents_reads_and_inference(monitor_backend, tmp_path):
    state = monitor_backend
    client = OpenCodeClient(replace(state.settings, catalog_root=tmp_path / "missing"))
    try:
        with pytest.raises(DomainError) as caught:
            await client.prompt("ses_monitor_transport", "monitor", state.payload, MonitorOutput)
        assert caught.value.code == "MONITOR_SKILLS_UNAVAILABLE"
        assert state.calls == []
    finally:
        await client.close()


async def test_readiness_never_preloads_business_data_or_skills(monitor_backend, tmp_path):
    state = monitor_backend
    client = OpenCodeClient(replace(state.settings, catalog_root=tmp_path / "missing"))
    await client.http.aclose()
    client.http = httpx.AsyncClient(
        base_url="http://localhost", transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json=structured_message())
        ),
    )
    try:
        output = await client.prompt(
            "ses_monitor_transport", "monitor", {"readiness_probe": True}, MonitorOutput
        )
        assert output.assessment == "NORMAL_STABLE"
        assert state.calls == []
        assert audit_reads(state) == []
    finally:
        await client.close()
