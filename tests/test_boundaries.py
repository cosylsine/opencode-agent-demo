import asyncio
import json

import pytest
from sqlalchemy import select

from apps.orchestrator.config import Settings
from apps.orchestrator.engine import Engine
from packages.adapters.example import ExampleEvaluator, ExampleObservationSource
from packages.adapters.opencode import OpenCodeClient
from packages.adapters.storage import SeriesStore, Store, events
from packages.contracts.agents import ControlOutput, MonitorOutput
from packages.domain.authorization import Tokens
from packages.domain.core import DomainError
from tests.test_workflow import execute


def test_scope_and_expiry():
    tokens = Tokens()
    token = tokens.issue("run", "request", None, "monitor")
    assert tokens.authorize(token, "detect_state").run_id == "run"
    with pytest.raises(DomainError):
        tokens.authorize(token, "get_tuning_context")
    expired = tokens.issue("run", "request", None, "monitor", ttl=-1)
    with pytest.raises(DomainError):
        tokens.authorize(expired, "detect_state")
    tokens.revoke(token)
    with pytest.raises(DomainError):
        tokens.authorize(token, "detect_state")
    assert token not in repr(tokens.scopes)


def test_process_lock(tmp_path):
    store = Store(tmp_path)
    try:
        with pytest.raises(DomainError, match="PROCESS_ALREADY_RUNNING"):
            Store(tmp_path)
    finally:
        store.close()
    Store(tmp_path).close()


def test_opencode_final_correlation():
    result = MonitorOutput(
        schema_version="1.0",
        assessment="NORMAL_STABLE",
        confidence=0.9,
        needs_tuning=False,
        manual_review_required=False,
        evidence=[],
        reason="test",
        warnings=[],
    ).model_dump(mode="json")
    messages = [
        {"info": {"id": "msg_user", "sessionID": "ses_test", "role": "user"}},
        {
            "info": {
                "id": "msg_tool",
                "sessionID": "ses_test",
                "role": "assistant",
                "finish": "tool-calls",
            }
        },
        {
            "info": {
                "id": "msg_final",
                "sessionID": "ses_test",
                "role": "assistant",
                "agent": "monitor",
                "parentID": "msg_user",
                "time": {"completed": 1},
                "finish": "stop",
                "structured": result,
            }
        },
    ]
    assert (
        OpenCodeClient.extract(messages, "ses_test", MonitorOutput, "monitor").assessment
        == "NORMAL_STABLE"
    )
    messages[-1]["info"]["parentID"] = "msg_other"
    with pytest.raises(DomainError):
        OpenCodeClient.extract(messages, "ses_test", MonitorOutput, "monitor")
    messages[-1]["info"]["parentID"] = "msg_user"
    with pytest.raises(DomainError):
        OpenCodeClient.extract(messages + [messages[-1]], "ses_test", MonitorOutput, "monitor")


def test_model_explanations_must_be_chinese():
    output = MonitorOutput(
        schema_version="1.0", assessment="NORMAL_STABLE", confidence=1.0,
        needs_tuning=False, manual_review_required=False, evidence=[],
        reason="stable", warnings=[],
    )
    with pytest.raises(DomainError, match="模型解释未使用中文"):
        OpenCodeClient.ensure_chinese(output)
    assert OpenCodeClient.ensure_chinese(
        output.model_copy(update={"reason": "系统稳定"})
    ).reason == "系统稳定"


def test_structured_tool_result_requires_bound_parent():
    result = MonitorOutput(
        schema_version="1.0",
        assessment="NORMAL_STABLE",
        confidence=1,
        needs_tuning=False,
        manual_review_required=False,
        evidence=[],
        reason="ready",
        warnings=[],
    )
    message = {
        "info": {
            "role": "assistant",
            "sessionID": "ses_test",
            "parentID": OpenCodeClient.message_id("ses_test"),
            "agent": "monitor",
            "time": {"completed": 1},
            "finish": "tool-calls",
            "structured": result.model_dump(mode="json"),
        },
        "parts": [{"type": "tool", "tool": "StructuredOutput", "state": {"status": "completed"}}],
    }
    assert OpenCodeClient.extract_latest(message, "ses_test", MonitorOutput, "monitor") == result
    message["info"]["parentID"] = "msg_other"
    with pytest.raises(DomainError, match="RESULT_UNKNOWN"):
        OpenCodeClient.extract_latest(message, "ses_test", MonitorOutput, "monitor")


def test_control_nested_pid_strings_are_strictly_validated():
    message = {
        "info": {
            "role": "assistant",
            "sessionID": "ses_control",
            "parentID": OpenCodeClient.message_id("ses_control"),
            "agent": "control",
            "time": {"completed": 1},
            "finish": "tool-calls",
            "structured": {
                "schema_version": "1.0",
                "decision": "PROPOSE",
                "delta": '{"kp":0.1,"ki":0.0,"kd":0.0}',
                "candidate_pid": '{"kp":1.1,"ki":0.2,"kd":0.05}',
                "reason": "test",
                "expected_effect": "test",
                "risk_notes": "[]",
            },
        },
        "parts": [{"type": "tool", "tool": "StructuredOutput", "state": {"status": "completed"}}],
    }
    output = OpenCodeClient.extract_latest(message, "ses_control", ControlOutput, "control")
    assert output.candidate_pid.kp == 1.1
    message["info"]["structured"]["risk_notes"] = "仅在离线模拟器评测"
    assert OpenCodeClient.extract_latest(
        message, "ses_control", ControlOutput, "control"
    ).risk_notes == ["仅在离线模拟器评测"]
    message["info"]["structured"]["candidate_pid"] = '{"kp":"unsafe","ki":0.2,"kd":0.05}'
    with pytest.raises(DomainError, match="AGENT_OUTPUT_INVALID"):
        OpenCodeClient.extract_latest(message, "ses_control", ControlOutput, "control")


@pytest.mark.parametrize("steps", [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14])
async def test_restart_at_committed_boundaries(tmp_path, steps):
    db, engine, evaluator, fake, run = await execute(tmp_path, steps=steps)
    await evaluator.close()
    db.close()
    db = Store(tmp_path)
    series = SeriesStore(db)
    evaluator = ExampleEvaluator(db, series)
    engine = Engine(
        db,
        series,
        ExampleObservationSource(series),
        evaluator,
        fake,
        Tokens(),
        Settings(state_dir=tmp_path),
    )
    await engine.drive(run.run_id)
    assert db.get(run.run_id).status == "SUCCEEDED"
    with db.engine.connect() as conn:
        assert (
            len(conn.execute(select(events).where(events.c.event == "CANDIDATE_ACCEPTED")).all())
            == 1
        )
    await evaluator.close()
    db.close()


async def test_timeout_and_terminal_immutable(tmp_path):
    db, engine, evaluator, fake, run = await execute(tmp_path, steps=1)
    run.deadline = "2000-01-01T00:00:00Z"
    db.save(run, run.state_version, "TEST_DEADLINE")
    await engine.drive(run.run_id)
    done = db.get(run.run_id)
    assert done.status == "STOPPED_TIMEOUT"
    done.status = "SUCCEEDED"
    with pytest.raises(DomainError):
        db.save(done, done.state_version, "LATE_RESULT")
    await evaluator.close()
    db.close()


async def test_result_file_before_registration(tmp_path):
    db, engine, evaluator, fake, run = await execute(tmp_path, steps=5)
    job = db.job(trial_id=run.baseline_trial_id)
    path = tmp_path / "jobs" / job.trial_id / "attempt.json"
    count = path.read_text()
    from packages.adapters.storage import jobs

    job.status = "RUNNING"
    job.series = None
    job.finished_at = None
    with db.engine.begin() as conn:
        conn.execute(
            jobs.update().where(jobs.c.id == job.trial_id).values(data=job.model_dump(mode="json"))
        )
    await evaluator.close()
    evaluator = ExampleEvaluator(db, engine.series)
    engine.evaluator = evaluator
    await engine.drive(run.run_id)
    # completed run may not need that baseline again: explicitly query the recovered job
    await evaluator.submit(job.request)
    await asyncio.gather(*evaluator.tasks.values())
    assert (await evaluator.get(job.trial_id)).status == "COMPLETED"
    assert path.read_text() == count
    await evaluator.close()
    db.close()


async def test_unknown_request_no_repost(tmp_path):
    db, engine, evaluator, fake, run = await execute(tmp_path, steps=2)
    from packages.contracts.common import now, uid
    from packages.contracts.internal import AgentAttempt

    attempt = AgentAttempt(
        schema_version="1.0",
        request_id=uid(),
        run_id=run.run_id,
        iteration_id=None,
        role="monitor",
        status="REQUESTED",
        session_id="ses_lost",
        message_id=None,
        created_at=now(),
        deadline=run.deadline,
        payload_hash="x",
        payload_redacted="{}",
        output=None,
        error_code=None,
    )
    db.save_attempt(attempt)
    await engine.drive(run.run_id)
    assert db.get(run.run_id).stop_reason == "RESULT_UNKNOWN"
    assert not fake.calls
    await evaluator.close()
    db.close()


async def test_timeout_recovers_without_reposting():
    import httpx

    from apps.orchestrator.config import Settings

    calls = []
    result = MonitorOutput(
        schema_version="1.0",
        assessment="NORMAL_STABLE",
        confidence=1.0,
        needs_tuning=False,
        manual_review_required=False,
        evidence=[],
        reason="正常",
        warnings=[],
    )

    async def handle(request):
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            assert request.content and json.loads(request.content)["messageID"] == OpenCodeClient.message_id("ses_1")
            raise httpx.ReadTimeout("injected")
        if request.url.path == "/session/status":
            return httpx.Response(200, json={})
        assert request.url.params["limit"] == "1"
        return httpx.Response(
            200,
            json=[
                {
                    "info": {
                        "role": "assistant",
                        "agent": "monitor",
                        "parentID": OpenCodeClient.message_id("ses_1"),
                        "sessionID": "ses_1",
                        "finish": "stop",
                        "time": {"completed": 1},
                        "structured": result.model_dump(mode="json"),
                    }
                },
            ],
        )

    client = OpenCodeClient(Settings())
    await client.http.aclose()
    client.http = httpx.AsyncClient(
        base_url="http://localhost", transport=httpx.MockTransport(handle)
    )
    output = await client.prompt("ses_1", "monitor", {}, MonitorOutput)
    assert output == result
    assert sum(method == "POST" for method, _ in calls) == 1
    await client.close()


async def test_control_transport_decodes_model_json_strings():
    import httpx

    from apps.orchestrator.config import Settings

    async def handle(request):
        body = json.loads(request.content)
        assert body["format"]["schema"]["properties"]["risk_notes"]["type"] == "string"
        return httpx.Response(200, json={
            "info": {
                "role": "assistant", "agent": "control", "sessionID": "ses_control",
                "parentID": OpenCodeClient.message_id("ses_control"),
                "finish": "tool-calls", "time": {"completed": 1},
                "structured": {
                    "schema_version": "1.0", "decision": "PROPOSE",
                    "delta": '{"kp":0.1,"ki":0.0,"kd":0.0}',
                    "candidate_pid": '{"kp":1.1,"ki":0.2,"kd":0.05}',
                    "reason": "小步增加比例参数", "expected_effect": "降低稳态误差",
                    "risk_notes": "需验证超调",
                },
            },
            "parts": [
                {"type": "tool", "tool": "StructuredOutput", "state": {"status": "completed"}}
            ],
        })

    client = OpenCodeClient(Settings())
    await client.http.aclose()
    client.http = httpx.AsyncClient(base_url="http://localhost", transport=httpx.MockTransport(handle))
    try:
        output = await client.prompt("ses_control", "control", {}, ControlOutput)
        assert output.candidate_pid.kp == 1.1
        assert output.risk_notes == ["需验证超调"]
    finally:
        await client.close()


async def test_database_rejects_second_candidate_job(tmp_path):
    from sqlalchemy.exc import IntegrityError

    from packages.adapters.storage import jobs
    from packages.contracts.common import uid

    db, engine, evaluator, fake, run = await execute(tmp_path)
    with db.engine.connect() as conn:
        records = conn.execute(select(jobs.c.data)).scalars().all()
    candidate = next(x for x in records if x["request"]["purpose"] == "CANDIDATE")
    with pytest.raises(IntegrityError):
        with db.engine.begin() as conn:
            conn.execute(jobs.insert().values(id=uid(), key="different-key", data=candidate))
    await evaluator.close()
    db.close()
