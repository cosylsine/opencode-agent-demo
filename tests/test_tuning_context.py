"""Regressions for the 32K Qwen overflow and compaction incident at iteration eight."""

import json

import httpx
import pytest

from apps.orchestrator.config import Settings
from apps.orchestrator.tuning import TuningEngine, new_run
from packages.adapters.opencode import OpenCodeClient
from packages.adapters.storage import Store
from packages.contracts.agents import ControlOutput
from packages.contracts.common import uid
from packages.domain.authorization import Tokens
from packages.domain.core import DomainError


def test_maximum_history_is_bounded_without_dropping_scenarios_or_trials(tmp_path):
    with_store = Store(tmp_path)
    try:
        engine = TuningEngine(with_store, None, Tokens())
        run = new_run()
        with_store.add_tuning(run)
        engine.trial(run, run.baseline_pid, 0, None)
        baseline = run.trials[0]
        run.trials.extend(baseline.model_copy(update={"iteration": i, "trial_id": uid()})
                          for i in range(1, 13))
        run.rejections = [{
            "iteration": i, "reason": "参数边界或 delta 不一致",
            "agent_output": {"candidate_pid": {"kp": 1.4, "ki": 0.1, "kd": 0.3},
                             "delta": {"kp": 0.5, "ki": 0.05, "kd": 0.0},
                             "reason": "长篇解释" * 500, "risk_notes": ["风险说明" * 500] * 10},
        } for i in range(1, 13)]
        run.search_reviews = [{"iteration": 2, "reason": "指标方向不一致" * 250}]
        before = run.model_dump_json()
        payload = engine.payload(run, "request", "token")
        prompt = engine.prompt_payload(run, "request", "token")
        assert "history" not in prompt and "current_best" not in prompt
        assert len(json.dumps(prompt, ensure_ascii=False)) < 1000
        assert [t["iteration"] for t in payload["history"]] == list(range(13))
        for item in payload["history"]:
            assert item["pid"] == baseline.pid
            assert set(item["result"]["scenarios"]) == set(baseline.result["scenarios"])
            assert item["result"]["score"] == pytest.approx(baseline.result["score"], rel=1e-7)
            for scenario in baseline.result["scenarios"]:
                # Preserve tiny conservation errors using significant figures,
                # and preserve every metric and safety flag for all scenarios.
                original = baseline.result["scenarios"][scenario]
                current = item["result"]["scenarios"][scenario]
                assert current.keys() == original.keys()
                assert current["mass_balance_max_error_kg"] > 0
        assert len(payload["rejections"]) == 12
        assert payload["rejections"][0]["candidate_pid"] == run.rejections[0]["agent_output"]["candidate_pid"]
        assert "agent_output" not in payload["rejections"][0]
        # Includes both a full 13-trial history and 12 maximal rejection outputs.
        assert len(json.dumps(payload, ensure_ascii=False).encode()) < 32000
        assert run.model_dump_json() == before
    finally:
        with_store.close()


def final_message(parent=None, **extra):
    return {"info": {
        "role": "assistant", "sessionID": "ses_context", "agent": "control",
        "parentID": parent or OpenCodeClient.message_id("ses_context"),
        "time": {"completed": 1}, "finish": "stop", **extra,
    }, "parts": []}


@pytest.mark.parametrize("error", [
    {"name": "ContextOverflowError", "data": {}},
    {"name": "APIError", "data": {"isRetryable": False, "message":
        "This model's maximum context length is 32768 tokens. However, you requested "
        "8192 output tokens and your prompt contains at least 24577 input tokens."}},
])
def test_context_errors_have_explicit_code(error):
    with pytest.raises(DomainError) as raised:
        OpenCodeClient.extract_latest(final_message(error=error), "ses_context", ControlOutput, "control")
    assert raised.value.code == "AGENT_CONTEXT_TOO_LARGE"


async def test_compaction_is_reported_without_rebinding_or_reposting():
    calls = []

    def handle(request):
        calls.append((request.method, request.url.path))
        if request.method == "POST":
            return httpx.Response(200, json=final_message(parent="msg_synthetic"))
        assert request.url.path == "/session/ses_context/message/msg_synthetic"
        return httpx.Response(200, json={
            "info": {"id": "msg_synthetic", "sessionID": "ses_context", "role": "user"},
            "parts": [{"type": "text", "synthetic": True, "text": "Continue after compaction"}],
        })

    client = OpenCodeClient(Settings())
    await client.http.aclose()
    client.http = httpx.AsyncClient(base_url="http://localhost", transport=httpx.MockTransport(handle))
    try:
        with pytest.raises(DomainError) as raised:
            await client.prompt("ses_context", "control", {}, ControlOutput)
        assert raised.value.code == "AGENT_CONTEXT_COMPACTED"
        assert sum(method == "POST" for method, _ in calls) == 1
    finally:
        await client.close()


def test_plain_json_text_is_not_accepted_as_a_structured_candidate():
    message = final_message()
    message["parts"] = [{"type": "text", "text": '{"decision":"PROPOSE"}'}]
    with pytest.raises(DomainError) as raised:
        OpenCodeClient.extract_latest(message, "ses_context", ControlOutput, "control")
    assert raised.value.code == "AGENT_OUTPUT_INVALID"
