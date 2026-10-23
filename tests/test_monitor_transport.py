import json

import httpx
import pytest
from pydantic import ValidationError

from apps.orchestrator.config import Settings
from packages.adapters.opencode import MONITOR_TRANSPORT_LIMITS, OpenCodeClient
from packages.contracts.agents import MonitorOutput
from packages.domain.core import DomainError


def structured_message(evidence="[]", warnings="[]"):
    return {
        "info": {
            "role": "assistant", "agent": "monitor", "sessionID": "ses_monitor_transport",
            "parentID": OpenCodeClient.message_id("ses_monitor_transport"),
            "finish": "tool-calls", "time": {"completed": 1},
            "structured": {
                "schema_version": "1.0", "assessment": "NORMAL_STABLE", "confidence": 0.95,
                "needs_tuning": False, "manual_review_required": False,
                "reason": "冻结窗口显示压力稳定", "evidence": evidence, "warnings": warnings,
            },
        },
        "parts": [{"type": "tool", "tool": "StructuredOutput", "state": {"status": "completed"}}],
    }


def extract(message):
    return OpenCodeClient.extract_latest(
        message, "ses_monitor_transport", MonitorOutput, "monitor"
    )


@pytest.mark.parametrize("readiness", [False, True])
async def test_monitor_transport_only_changes_model_facing_nested_fields(readiness, monkeypatch):
    original_schema = MonitorOutput.model_json_schema()
    evidence = [{"feature": "steady_state_error", "value": 0.01, "interpretation": "稳态误差较小"}]

    def respond(request):
        body = json.loads(request.content)
        properties = body["format"]["schema"]["properties"]
        for key, limit in MONITOR_TRANSPORT_LIMITS.items():
            assert properties[key]["type"] == "string"
            assert properties[key]["maxLength"] == limit
        for key in set(properties) - set(MONITOR_TRANSPORT_LIMITS):
            assert properties[key] == original_schema["properties"][key]
        assert "Control" not in body["system"]
        assert "candidate_pid" not in body["system"]
        assert "delta" not in body["system"]
        for name in ("get_recent_window", "calculate_metrics", "detect_state"):
            assert name in body["system"]
            assert body["tools"]["industrial_control_" + name] is False
        assert "steam-system-monitoring" in body["system"]
        assert "control-safety" in body["system"]
        assert body["format"]["retryCount"] == 0
        assert body.get("variant") == ("readiness" if readiness else None)
        return httpx.Response(200, json=structured_message(
            json.dumps(evidence, ensure_ascii=False), '["仅依据示例数据"]'
        ))

    client = OpenCodeClient(Settings())
    async def prepared(payload):
        return payload, {"steam-system-monitoring": {"sha256": "hash", "content": "监测技能"}}
    monkeypatch.setattr(client, "prepare_monitor", prepared)
    await client.http.aclose()
    client.http = httpx.AsyncClient(base_url="http://localhost", transport=httpx.MockTransport(respond))
    try:
        output = await client.prompt(
            "ses_monitor_transport", "monitor", {"readiness_probe": readiness}, MonitorOutput
        )
        assert output.evidence[0].value == 0.01
        assert output.warnings == ["仅依据示例数据"]
        assert MonitorOutput.model_json_schema() == original_schema
        assert original_schema["properties"]["evidence"]["type"] == "array"
        with pytest.raises(ValidationError):
            MonitorOutput.model_validate(structured_message()["info"]["structured"])
    finally:
        await client.close()


@pytest.mark.parametrize("evidence,warnings", [
    ([], []),
    ("[]", "[]"),
    ('[{"feature":"error","value":0.0,"interpretation":"误差为零"}]', '["示例数据"]'),
])
def test_original_arrays_and_explicit_json_array_transport_are_supported(evidence, warnings):
    assert isinstance(extract(structured_message(evidence, warnings)), MonitorOutput)


@pytest.mark.parametrize("evidence,warnings", [
    ("plain text", "[]"),
    ('{"feature":"error"}', "[]"),
    ("null", "[]"),
    ("[]", "这不是 JSON 数组"),
    ("[]", '"单个 JSON 字符串也不接受"'),
    ("[]", "[1]"),
    ('[{"feature":"error","value":"0.0","interpretation":"文本"}]', "[]"),
    ('[{"feature":"error","value":true,"interpretation":"文本"}]', "[]"),
    ('[{"feature":"error","value":NaN,"interpretation":"文本"}]', "[]"),
    ('[{"feature":"error","value":0.0,"interpretation":"文本","extra":1}]', "[]"),
    ("[]", json.dumps(["警告"] * 11)),
    (json.dumps([{"feature": "x", "value": 0.0, "interpretation": "说明"}] * 11), "[]"),
    ("[]", json.dumps(["警告" * 1001])),
    (" " * (MONITOR_TRANSPORT_LIMITS["evidence"] + 1), "[]"),
    ("[]", " " * (MONITOR_TRANSPORT_LIMITS["warnings"] + 1)),
])
def test_monitor_transport_rejects_invalid_types_bounds_and_free_text(evidence, warnings):
    with pytest.raises(DomainError, match="AGENT_OUTPUT_INVALID"):
        extract(structured_message(evidence, warnings))


def test_json_in_text_does_not_replace_structured_output():
    message = structured_message()
    text = json.dumps(message["info"].pop("structured"))
    message["info"]["finish"] = "stop"
    message["parts"] = [{"type": "text", "text": text}]
    with pytest.raises(DomainError) as caught:
        extract(message)
    assert caught.value.code == "AGENT_OUTPUT_INVALID"


def test_monitor_transport_keeps_binding_and_completion_checks():
    message = structured_message()
    message["info"]["parentID"] = "msg_unrelated"
    with pytest.raises(DomainError, match="RESULT_UNKNOWN"):
        extract(message)
    message = structured_message()
    message["parts"][0]["state"]["status"] = "running"
    with pytest.raises(DomainError, match="RESULT_UNKNOWN"):
        extract(message)
