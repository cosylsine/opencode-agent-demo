import asyncio
from unittest.mock import AsyncMock

import pytest

from apps.orchestrator.config import Settings
from packages.adapters import opencode
from packages.adapters.opencode import OpenCodeClient
from packages.contracts.agents import MonitorOutput
from packages.domain.core import DomainError


@pytest.fixture
async def client(monkeypatch):
    client = OpenCodeClient(Settings(
        provider="test", model="test", opencode_password="test", service_token="test"
    ))
    monkeypatch.setattr(opencode, "verify_permissions", lambda agents: None)

    async def api(method, path, **kwargs):
        return {
            "/global/health": {"version": "1.18.31"},
            "/agent": [],
            "/mcp": {"industrial_control": {"status": "connected"}},
        }[path]

    client.api = AsyncMock(side_effect=api)
    client.create_session = AsyncMock(return_value="ses_probe_timeout")
    client.prompt = AsyncMock()
    client.abort = AsyncMock(return_value=True)
    try:
        yield client
    finally:
        await client.close()


async def test_successful_probe_retains_validated_version_and_clears_failure(client):
    client.ready, client.error_code = False, "MODEL_PROBE_TIMEOUT"
    await client.probe(timeout_seconds=0.1)
    assert client.ready is True
    assert client.error_code is None
    assert client.version == "1.18.31"
    client.abort.assert_not_awaited()
    args = client.prompt.await_args.args
    assert args[0:2] == ("ses_probe_timeout", "monitor")
    assert args[2]["readiness_probe"] is True
    assert args[3] is MonitorOutput


async def test_continuously_active_probe_has_total_deadline_and_one_abort(client):
    ticks = []
    cancelled = asyncio.Event()

    async def active_prompt(*args, **kwargs):
        try:
            while True:
                ticks.append(1)
                await asyncio.sleep(0.001)
        finally:
            cancelled.set()

    client.ready, client.error_code = True, None
    client.prompt.side_effect = active_prompt
    await asyncio.wait_for(client.probe(timeout_seconds=0.02), timeout=0.5)
    assert len(ticks) >= 1 and cancelled.is_set()
    assert client.ready is False
    assert client.error_code == "MODEL_PROBE_TIMEOUT"
    client.abort.assert_awaited_once_with("ses_probe_timeout")
    client.create_session.assert_awaited_once()
    client.prompt.assert_awaited_once()


async def test_probe_timeout_before_session_creation_does_not_abort_unknown_session(client):
    client.ready = True

    async def stalled_api(*args, **kwargs):
        await asyncio.Event().wait()

    client.api.side_effect = stalled_api
    await asyncio.wait_for(client.probe(timeout_seconds=0.01), timeout=0.5)
    assert client.ready is False
    assert client.error_code == "MODEL_PROBE_TIMEOUT"
    client.create_session.assert_not_awaited()
    client.abort.assert_not_awaited()


async def test_probe_cancellation_aborts_owned_session_and_propagates(client):
    entered = asyncio.Event()

    async def stalled_prompt(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    client.ready, client.error_code = True, None
    client.prompt.side_effect = stalled_prompt
    task = asyncio.create_task(client.probe())
    await asyncio.wait_for(entered.wait(), timeout=0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=0.5)
    client.abort.assert_awaited_once_with("ses_probe_timeout")
    assert client.ready is False
    assert client.error_code == "MODEL_PROBE_CANCELLED"


@pytest.mark.parametrize("cancel", [False, True])
async def test_failed_cleanup_cannot_replace_probe_timeout_or_cancellation(client, cancel):
    entered = asyncio.Event()

    async def stalled_prompt(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    client.prompt.side_effect = stalled_prompt
    client.abort.side_effect = RuntimeError("cleanup unavailable")
    task = asyncio.create_task(client.probe(timeout_seconds=0.01 if not cancel else 1))
    await asyncio.wait_for(entered.wait(), timeout=0.5)
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=0.5)
    else:
        await asyncio.wait_for(task, timeout=0.5)
    assert client.ready is False
    assert client.error_code == ("MODEL_PROBE_CANCELLED" if cancel else "MODEL_PROBE_TIMEOUT")
    client.abort.assert_awaited_once_with("ses_probe_timeout")


async def test_abort_itself_is_bounded(client, monkeypatch):
    monkeypatch.setattr(opencode, "MODEL_PROBE_ABORT_TIMEOUT_SECONDS", 0.01)
    abort_cancelled = asyncio.Event()

    async def stalled_prompt(*args, **kwargs):
        await asyncio.Event().wait()

    async def stalled_abort(session):
        try:
            await asyncio.Event().wait()
        finally:
            abort_cancelled.set()

    client.prompt.side_effect = stalled_prompt
    client.abort.side_effect = stalled_abort
    await asyncio.wait_for(client.probe(timeout_seconds=0.01), timeout=0.5)
    assert abort_cancelled.is_set()
    assert client.error_code == "MODEL_PROBE_TIMEOUT"


async def test_existing_validation_failures_still_clear_previous_success(client):
    client.ready, client.error_code = True, None
    client.prompt.side_effect = DomainError("AGENT_OUTPUT_INVALID")
    await client.probe(timeout_seconds=0.1)
    assert client.ready is False
    assert client.error_code == "AGENT_OUTPUT_INVALID"
    client.abort.assert_not_awaited()


async def test_unconfigured_probe_clears_previous_success_without_network_calls(client):
    client.settings = Settings()
    client.ready, client.error_code = True, None
    await client.probe(timeout_seconds=0.1)
    assert client.ready is False
    assert client.error_code == "MODEL_NOT_CONFIGURED"
    client.api.assert_not_awaited()
    client.abort.assert_not_awaited()
