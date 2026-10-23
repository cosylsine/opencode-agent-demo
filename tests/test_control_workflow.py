"""Program constraints for preparation, context delivery and bounded recovery."""

import json
import shutil
from pathlib import Path

import httpx
import pytest

from apps.orchestrator.config import Settings
from apps.orchestrator.tuning import TuningEngine, new_run
from packages.adapters.control_workflow import ControlWorkflow, load_control_skills
from packages.adapters.opencode import OpenCodeClient
from packages.adapters.storage import Store
from packages.contracts.agents import ControlOutput
from packages.domain.authorization import Tokens
from packages.domain.core import DomainError
from tests.fakes import FakeClient
from tests.test_tuning import abstain, tuning_proposal

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize("damage", ["missing", "empty", "symlink", "too_large", "wrong_name"])
async def test_invalid_skills_block_inference_before_session_creation(tmp_path, damage):
    catalog = tmp_path / "catalog"
    shutil.copytree(ROOT / ".opencode/skills", catalog / ".opencode/skills")
    path = catalog / ".opencode/skills/pid-tuning/SKILL.md"
    if damage == "missing":
        path.unlink()
    elif damage == "empty":
        path.write_text("---\nname: pid-tuning\n---\n")
    elif damage == "symlink":
        path.unlink()
        path.symlink_to(ROOT / ".opencode/skills/pid-tuning/SKILL.md")
    elif damage == "too_large":
        path.write_text("---\nname: pid-tuning\n---\n" + "x" * 16384)
    else:
        path.write_text("---\nname: another-skill\n---\nrules")
    db = Store(tmp_path / "state")
    client = FakeClient()
    try:
        engine = TuningEngine(db, client, Tokens(), catalog)
        run = new_run()
        run.pending_iteration = 1
        db.add_tuning(run)
        with pytest.raises(DomainError) as raised:
            await engine.prepare_attempt(run)
        assert raised.value.code == "CONTROL_SKILLS_UNAVAILABLE"
        assert not client.sessions and not client.calls
    finally:
        db.close()


def message(receipt, decision="PROPOSE", kind=None):
    output = tuning_proposal() if decision == "PROPOSE" else abstain()
    return {"info": {
        "role": "assistant", "agent": "control", "sessionID": "ses_prepared",
        "parentID": OpenCodeClient.message_id("ses_prepared"), "time": {"completed": 1},
        "finish": "tool-calls", "structured": {
            **output.model_dump(mode="json"), "context_receipt": receipt, "abstain_kind": kind,
        },
    }, "parts": [{"type": "tool", "tool": "StructuredOutput", "state": {"status": "completed"}}]}


@pytest.mark.parametrize("receipt,decision,kind,expected", [
    ("correct", "PROPOSE", None, None),
    ("correct", "ABSTAIN", "NO_CANDIDATE", None),
    ("old-receipt", "PROPOSE", None, "CONTROL_WORKFLOW_INVALID"),
    (None, "ABSTAIN", "NO_CANDIDATE", "CONTROL_WORKFLOW_INVALID"),
    ("correct", "ABSTAIN", "WORKFLOW_ERROR", "CONTROL_WORKFLOW_INVALID"),
    ("correct", "ABSTAIN", None, "CONTROL_WORKFLOW_INVALID"),
    ("correct", "PROPOSE", "NO_CANDIDATE", "CONTROL_WORKFLOW_INVALID"),
])
def test_context_receipt_and_abstention_kind_are_validated(receipt, decision, kind, expected):
    workflow = ControlWorkflow("correct")
    if expected:
        with pytest.raises(DomainError) as raised:
            OpenCodeClient.extract_latest(message(receipt, decision, kind), "ses_prepared", ControlOutput, "control", workflow)
        assert raised.value.code == expected
    else:
        result = OpenCodeClient.extract_latest(message(receipt, decision, kind), "ses_prepared", ControlOutput, "control", workflow)
        assert result.decision == decision


async def test_preloaded_skills_are_injected_and_redundant_loading_disabled():
    workflow = ControlWorkflow("receipt-not-in-initial-prompt", load_control_skills(ROOT))

    def handle(request):
        body = json.loads(request.content)
        assert workflow.receipt not in request.content.decode()
        assert body["tools"]["skill"] is False
        assert body["tools"]["industrial_control_get_tuning_context"] is False
        for name, item in workflow.skills.items():
            assert name in body["system"] and item["content"] in body["system"]
        assert {"context_receipt", "abstain_kind"} <= set(body["format"]["schema"]["required"])
        return httpx.Response(200, json=message(workflow.receipt))

    client = OpenCodeClient(Settings())
    await client.http.aclose()
    client.http = httpx.AsyncClient(base_url="http://localhost", transport=httpx.MockTransport(handle))
    try:
        assert (await client.prompt("ses_prepared", "control", {}, ControlOutput, workflow=workflow)).decision == "PROPOSE"
    finally:
        await client.close()


async def ready_engine(tmp_path, client):
    db = Store(tmp_path)
    engine = TuningEngine(db, client, Tokens())
    run = new_run()
    db.add_tuning(run)
    for _ in range(3):
        await engine.step(db.get_tuning(run.run_id))
    return db, engine, run.run_id


async def test_unconfirmed_context_cannot_reach_model_and_retries_same_iteration(tmp_path):
    class SkippingClient(FakeClient):
        reads = 0

        async def control_context(self, token, workflow):
            self.reads += 1
            if self.reads == 1:
                return {"context_receipt": workflow.receipt}
            return await super().control_context(token, workflow)

    client = SkippingClient(controls=[tuning_proposal()])
    db, engine, rid = await ready_engine(tmp_path, client)
    try:
        old = db.get_tuning(rid)
        await engine.step(old)
        failed = db.get_tuning(rid)
        assert failed.status == "AGENT_RUNNING" and failed.pending_iteration == 1
        assert failed.agent_failures[-1]["error_code"] == "CONTROL_CONTEXT_UNAVAILABLE"
        assert len(failed.trials) == 1 and failed.no_improvement_count == 0
        assert not client.calls
        for _ in range(3):
            await engine.step(db.get_tuning(rid))
        current = db.get_tuning(rid)
        assert len(current.trials) == 2 and current.trials[-1].iteration == 1
        assert current.trials[-1].agent_session_id != old.session_id
        assert len(client.calls) == 1 and client.reads == 2
    finally:
        db.close()


async def test_duplicate_reads_are_small_and_request_state_survives_restart(tmp_path):
    db, engine, rid = await ready_engine(tmp_path, FakeClient())
    run = db.get_tuning(rid)
    run.agent_sent = True
    db.save_tuning(run)
    token = engine.tokens.issue(rid, run.request_id, "1", "control")
    scope = engine.tokens.authorize(token, "get_tuning_context")
    try:
        first = engine.read_context(scope)
        second = engine.read_context(scope)
        assert "history" in first["data"] and "history" not in second["data"]
        assert second["data"]["context_already_delivered"] is True
        assert first["data"]["context_receipt"] == second["data"]["context_receipt"]
        assert len(json.dumps(second, ensure_ascii=False)) < 400
        db.close()
        db = Store(tmp_path)
        resumed = TuningEngine(db, FakeClient(), Tokens())
        assert resumed.read_context(scope)["data"]["context_already_delivered"] is True
        run = db.get_tuning(rid)
        run.prepared_skill_hashes = {}
        db.save_tuning(run)
        with pytest.raises(DomainError) as raised:
            resumed.read_context(scope)
        assert raised.value.code == "CONTROL_NOT_PREPARED"
    finally:
        db.close()


@pytest.mark.parametrize("code", ["CONTROL_WORKFLOW_INVALID", "AGENT_OUTPUT_INVALID", "RESULT_UNKNOWN"])
async def test_attempts_are_bounded_and_unknown_requests_are_not_resent(tmp_path, code):
    client = FakeClient(controls=[DomainError(code)] * 3)
    db, engine, rid = await ready_engine(tmp_path, client)
    try:
        with pytest.raises(DomainError) as raised:
            for _ in range(6):
                await engine.step(db.get_tuning(rid))
        if code == "RESULT_UNKNOWN":
            assert raised.value.code == "RESULT_UNKNOWN"
            assert len(client.calls) == 1
        else:
            assert raised.value.code == "CONTROL_WORKFLOW_FAILED"
            run = db.get_tuning(rid)
            assert len(run.agent_failures) == 3 and len(client.calls) == 3
            assert len({item["session_id"] for item in run.agent_failures}) == 3
            assert run.pending_iteration == 1 and len(run.trials) == 1
            assert run.no_improvement_count == 0
    finally:
        db.close()


async def test_context_read_failure_is_bounded_before_any_model_prompt(tmp_path):
    class UnavailableClient(FakeClient):
        reads = 0

        async def control_context(self, token, workflow):
            self.reads += 1
            raise DomainError("CONTROL_CONTEXT_UNAVAILABLE")

    client = UnavailableClient()
    db, engine, rid = await ready_engine(tmp_path, client)
    try:
        with pytest.raises(DomainError, match="CONTROL_CONTEXT_UNAVAILABLE"):
            for _ in range(6):
                await engine.step(db.get_tuning(rid))
        assert client.reads == 3 and not client.calls
        assert not db.get_tuning(rid).agent_sent
    finally:
        db.close()


async def test_program_provides_context_once_and_keeps_live_token_out_of_prompt(tmp_path):
    class RecordingClient(FakeClient):
        token = None

        async def control_context(self, token, workflow):
            self.token = token
            return await super().control_context(token, workflow)

    client = RecordingClient(controls=[tuning_proposal()])
    db, engine, rid = await ready_engine(tmp_path, client)
    try:
        await engine.step(db.get_tuning(rid))
        payload = client.calls[0][1]
        assert client.token not in json.dumps(payload)
        assert "call_context_token" not in payload
        assert "call_context_token" not in payload["control_context"]
        assert "history" not in payload and len(payload["control_context"]["history"]) == 1
        with pytest.raises(DomainError):
            engine.tokens.authorize(client.token, "get_tuning_context")
        assert db.get_tuning(rid).status == "TRIAL_RUNNING"
    finally:
        db.close()
