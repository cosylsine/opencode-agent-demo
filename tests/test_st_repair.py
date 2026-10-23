import json

import httpx
import pytest

from apps.orchestrator.config import Settings
from packages.adapters import st_model
from packages.adapters.st_code import validate_result
from packages.adapters.st_scan_eval import Machine
from packages.adapters.st_workflow import (
    OVERLAPPING_EDITS,
    apply_edits,
    overlapping_bug_evidence,
    run_workflow,
    single_repair_plan,
    stage_call,
)
from packages.contracts.st_code import STBug, STRepairStage, STStart
from packages.domain.core import DomainError

SOURCE = """PROGRAM Gate
VAR
    Ready : BOOL;
    Permit : BOOL;
    Output : INT;
    Valid : BOOL;
END_VAR
IF Ready THEN
    Output := 8;
    Valid := TRUE;
END_IF;
END_PROGRAM
"""


def bug(number, start, end, **changes):
    fields = dict(
        id=f"B{number:03d}", title="测试缺陷", severity="high", confidence="confirmed",
        line_start=start, line_end=end, evidence="IF Ready THEN",
        explanation="测试证据", suggestion="按工程约定修复", disposition="not_fixed",
        fix_explanation="尚未修复",
    )
    fields.update(changes)
    return STBug(**fields)


def test_overlap_hints_merge_transitively_and_exclude_fixed_or_possible_findings():
    bugs = [
        bug(1, 12, 15), bug(2, 13, 13), bug(3, 15, 18), bug(4, 19, 20),
        bug(5, 1, 30, confidence="possible", disposition="needs_context"),
        bug(6, 1, 30, disposition="fixed"),
    ]
    original = [value.model_dump() for value in bugs]
    assert overlapping_bug_evidence(list(reversed(bugs))) == [
        {"line_start": 12, "line_end": 18, "bug_ids": ["B001", "B002", "B003"]},
    ]
    assert [value.model_dump() for value in bugs] == original


@pytest.mark.asyncio
@pytest.mark.parametrize("response,error", [
    ("merged", None),
    ("still_overlapping", "单整合补丁必须恰好一项"),
    ("wrong_range", "单整合补丁必须恰好一项"),
    ("unknown_bug", "单整合补丁只能关联"),
    ("changed_declaration", "补丁改变了 POU 名称"),
])
async def test_overlapping_repairs_require_real_merge_and_remain_bounded(response, error):
    request = STStart(
        schema_version="1.0", code=SOURCE, actions=["fix"],
        instructions="Ready且Permit为TRUE时Output=8且Valid=TRUE；其他情况Output=0、Valid=FALSE。",
    )
    calls, progress = [], []
    wrong = [
        dict(line_start=8, line_end=11,
             replacement="IF Ready THEN\nOutput := 8;\nValid := TRUE;\n"
             "ELSE\nOutput := 0;\nValid := FALSE;\nEND_IF;",
             bug_ids=["B001"], explanation="补充复位"),
        dict(line_start=8, line_end=8, replacement="IF Ready AND Permit THEN",
             bug_ids=["B002"], explanation="检查许可"),
    ]
    merged = dict(
        line_start=8, line_end=11,
        replacement="IF Ready AND Permit THEN\nOutput := 8;\nValid := TRUE;\n"
        "ELSE\nOutput := 0;\nValid := FALSE;\nEND_IF;",
        bug_ids=["B001", "B002"], explanation="同一控制块统一处理许可与输出复位",
    )
    if response == "wrong_range":
        merged["line_start"] = 2
    elif response == "unknown_bug":
        merged["bug_ids"] = ["B999"]
    elif response == "changed_declaration":
        merged["replacement"] = "VAR\nExtra : INT;\nEND_VAR\n" + merged["replacement"]

    class Client:
        async def st_generate(self, schema, payload, instruction, request_id):
            calls.append(payload)
            if payload["stage"] == "review":
                return schema(
                    **payload["source_receipt"], summary="两项逻辑缺陷", assumptions=[],
                    findings=[
                        dict(title="非就绪时未复位", severity="high", confidence="confirmed",
                             line_start=8, line_end=11, explanation="缺少ELSE赋值",
                             suggestion="非就绪时清零输出"),
                        dict(title="未检查许可", severity="high", confidence="confirmed",
                             line_start=8, line_end=8, explanation="条件没有检查Permit",
                             suggestion="同时检查Permit"),
                    ],
                )
            assert payload["stage"] == "repair"
            assert payload["overlapping_bug_evidence"] == [
                {"line_start": 8, "line_end": 11, "bug_ids": ["B002", "B001"]},
            ]
            assert payload["single_edit_repair"] == {
                "line_start": 8, "line_end": 11, "allowed_bug_ids": ["B001", "B002"],
                "source_lines": payload["repair_source_lines"][7:11],
            }
            if len(calls) == 2:
                assert not payload["correction"]
                return schema(**payload["source_receipt"], edits=wrong)
            assert "单整合补丁必须恰好一项" in payload["correction"]
            assert payload["previous_output"]["edits"] == wrong
            assert "不要保留被合并的旧补丁" in instruction
            assert "不要重新设计整组用例" not in instruction
            assert "禁止返回完整源文件" not in instruction
            assert "不复制整份源代码" not in instruction
            assert "禁止返回整个源文件或加入范围外的语句" in instruction
            return schema(**payload["source_receipt"],
                          edits=wrong if response == "still_overlapping" else [merged])

    async def run():
        return await run_workflow(Client(), request, lambda session, **value: progress.append(value))

    if not error:
        result = await run()
        assert all(value.disposition == "fixed" for value in result.bugs)
        assert result.fixed_code.count("IF Ready AND Permit THEN") == 1
        # Exercise the combined correction over consecutive scans, not just its JSON shape.
        machine = Machine(result.fixed_code)
        for ready, permit, output in [(True, True, 8), (True, False, 0), (True, True, 8),
                                     (False, True, 0)]:
            machine.state.update(READY=ready, PERMIT=permit)
            machine.scan()
            assert machine.state["OUTPUT"] == output
            assert machine.state["VALID"] is bool(output)
        checks = validate_result(request, result)
        assert checks.compiler == checks.tests == "not_run"
        assert checks.scan_verification == "not_run"
    else:
        with pytest.raises(DomainError, match="两次结果未通过校验：" + error):
            await run()
    assert [payload["stage"] for payload in calls] == ["review", "repair", "repair"]
    repairs = [event for event in progress if event.get("stage") == "repair"]
    assert len([event for event in repairs if "request_id" in event]) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("single_block", [False, True])
async def test_native_repair_schema_limits_prose_without_truncating_code_or_mutating_contract(
    monkeypatch, single_block,
):
    monkeypatch.setattr(st_model, "model_connection", lambda settings: (
        "http://model.invalid/v1/chat/completions", "synthetic-test-key",
    ))
    original_schema = STRepairStage.model_json_schema()
    sent = []

    def handler(request):
        body = json.loads(request.content)
        sent.append(body)
        schema = body["response_format"]["json_schema"]["schema"]
        props = schema["$defs"]["STSourceEdit"]["properties"]
        assert props["explanation"]["maxLength"] == 240
        assert props["replacement"]["maxLength"] == 12000
        assert body["max_tokens"] == 4096
        if single_block:
            assert schema["properties"]["edits"]["minItems"] == 1
            assert schema["properties"]["edits"]["maxItems"] == 1
            assert props["line_start"]["const"] == 8
            assert props["line_end"]["const"] == 11
            assert props["bug_ids"]["items"]["enum"] == ["B001"]
        else:
            assert schema["properties"]["edits"]["maxItems"] == 40
            assert "const" not in props["line_end"]
            assert "enum" not in props["bug_ids"]["items"]
        edits = [dict(
            line_start=8, line_end=11, replacement="IF Ready THEN\nValid := TRUE;\nEND_IF;",
            bug_ids=["B001"], explanation="模型测试响应",
        )] if single_block else []
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "stop", "message": {"content": json.dumps({
                "source_line_count": 12, "source_last_line": "END_PROGRAM", "edits": edits,
            })},
        }]})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(st_model.httpx, "AsyncClient", lambda **kwargs: original_client(
        **kwargs, transport=httpx.MockTransport(handler),
    ))
    payload = {"single_edit_repair": {
        "line_start": 8, "line_end": 11, "allowed_bug_ids": ["B001"],
    }} if single_block else {}
    result = await st_model.generate(
        Settings(provider="local", model="test"), STRepairStage, payload, "测试说明", "repair-test",
    )
    assert bool(result.edits) is single_block
    assert len(sent) == 1
    assert STRepairStage.model_json_schema() == original_schema


@pytest.mark.asyncio
@pytest.mark.parametrize("second_finishes", [False, True])
async def test_known_overlap_uses_same_native_block_schema_after_length_error(
    monkeypatch, second_finishes,
):
    monkeypatch.setattr(st_model, "model_connection", lambda settings: (
        "http://model.invalid/v1/chat/completions", "synthetic-test-key",
    ))
    request = STStart(schema_version="1.0", code=SOURCE, actions=["fix"])
    bugs = [bug(1, 8, 11), bug(2, 8, 8)]
    payload = {
        "repair_source_lines": [dict(line=i, text=text)
                                for i, text in enumerate(SOURCE.splitlines(), 1)],
        "bugs": [item.model_dump() for item in bugs],
        "overlapping_bug_evidence": overlapping_bug_evidence(bugs),
    }
    sent, progress = [], []
    replacement = "IF Ready AND Permit THEN\nOutput := 8;\nValid := TRUE;\n"
    replacement += "ELSE\nOutput := 0;\nValid := FALSE;\nEND_IF;"

    def handler(request):
        body = json.loads(request.content)
        sent.append(body)
        finished = len(sent) == 2 and second_finishes
        content = json.dumps({
            "source_line_count": 12, "source_last_line": "END_PROGRAM", "edits": [dict(
                line_start=8, line_end=11, replacement=replacement,
                bug_ids=["B001", "B002"], explanation="合并控制块修复",
            )],
        }) if finished else '{"source_line_count":12,"edits":['
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "stop" if finished else "length", "message": {"content": content},
        }]})

    original_client = httpx.AsyncClient
    monkeypatch.setattr(st_model.httpx, "AsyncClient", lambda **kwargs: original_client(
        **kwargs, transport=httpx.MockTransport(handler),
    ))

    class Client:
        async def st_generate(self, schema, payload, instruction, request_id):
            return await st_model.generate(
                Settings(provider="local", model="test"), schema, payload, instruction, request_id,
            )

    async def run():
        return await stage_call(
            Client(), request, "repair", STRepairStage, payload, "普通补丁说明",
            lambda session, **value: progress.append(value),
            lambda value: apply_edits(SOURCE, bugs, value.edits),
        )

    if second_finishes:
        fixed, updated = await run()
        assert fixed.startswith("\n".join(SOURCE.splitlines()[:7]) + "\n")
        assert fixed.endswith("\nEND_PROGRAM\n")
        assert all(item.disposition == "fixed" for item in updated)
        machine = Machine(fixed)
        for ready, permit, output in [(True, True, 8), (True, False, 0), (False, True, 0)]:
            machine.state.update(READY=ready, PERMIT=permit)
            machine.scan()
            assert machine.state["OUTPUT"] == output
            assert machine.state["VALID"] is bool(output)
    else:
        with pytest.raises(DomainError, match="两次结果未通过校验：当前阶段输出达到长度限制"):
            await run()
    assert len(sent) == 2
    assert sent[0]["response_format"] == sent[1]["response_format"]
    schema = sent[0]["response_format"]["json_schema"]["schema"]
    assert schema["properties"]["edits"]["minItems"] == schema["properties"]["edits"]["maxItems"] == 1
    edit = schema["$defs"]["STSourceEdit"]["properties"]
    assert edit["line_start"]["const"] == 8 and edit["line_end"]["const"] == 11
    assert edit["bug_ids"]["items"]["enum"] == ["B001", "B002"]
    first, second = [json.loads(body["messages"][1]["content"]) for body in sent]
    assert first["single_edit_repair"] == second["single_edit_repair"]
    assert first["correction"] == "" and "长度限制" in second["correction"]
    assert "previous_output" not in first and "previous_output" not in second
    assert len([event for event in progress if "output" in event]) == int(second_finishes)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["no_overlap", "possible", "long", "unsafe"])
async def test_first_attempt_block_plan_is_disabled_when_evidence_or_source_is_unsuitable(case):
    source = SOURCE
    bugs = [bug(1, 8, 11), bug(2, 8, 8)]
    if case == "no_overlap":
        bugs = [bug(1, 9, 9), bug(2, 10, 10)]
    elif case == "possible":
        bugs[1] = bug(2, 8, 8, confidence="possible", disposition="needs_context")
    elif case == "long":
        source += "(*" + "x" * 4000 + "*)\n"
    else:
        source = source.replace("END_IF;", "END_WHILE;")
    request = STStart(schema_version="1.0", code=source, actions=["fix"])
    payload = {
        "repair_source_lines": [dict(line=i, text=text)
                                for i, text in enumerate(source.splitlines(), 1)],
        "bugs": [item.model_dump() for item in bugs],
        "overlapping_bug_evidence": overlapping_bug_evidence(bugs),
    }
    calls = []

    class Client:
        async def st_generate(self, schema, payload, instruction, request_id):
            calls.append(payload)
            assert "single_edit_repair" not in payload
            raise DomainError("ST_OUTPUT_INVALID", "当前阶段输出达到长度限制", 503)

    with pytest.raises(DomainError, match="两次结果未通过校验：当前阶段输出达到长度限制"):
        await stage_call(Client(), request, "repair", STRepairStage, payload, "原阶段说明",
                         lambda *args, **kwargs: None, lambda value: value)
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("whole_file", [False, True])
async def test_known_overlap_revalidates_cached_patch_boundaries(whole_file):
    request = STStart(schema_version="1.0", code=SOURCE, actions=["fix"])
    bugs = [bug(1, 8, 8), bug(2, 8, 8)]
    payload = {
        "repair_source_lines": [dict(line=i, text=text)
                                for i, text in enumerate(SOURCE.splitlines(), 1)],
        "bugs": [item.model_dump() for item in bugs],
        "overlapping_bug_evidence": overlapping_bug_evidence(bugs),
    }
    receipt = dict(source_line_count=12, source_last_line="END_PROGRAM")
    partial = dict(
        line_start=8, line_end=8, replacement="IF Ready AND Permit THEN",
        bug_ids=["B001", "B002"], explanation="合并条件修复",
    )
    edit = dict(partial, line_start=1, line_end=12,
                replacement=SOURCE.replace("IF Ready THEN", partial["replacement"])) if whole_file else partial
    calls, events = [], []

    def progress(session, **value):
        events.append(value)

    progress.checkpoint = {"repair": dict(**receipt, edits=[edit]), "tests": {"stale": True}}

    class Client:
        async def st_generate(self, schema, payload, instruction, request_id):
            calls.append(payload)
            assert whole_file
            assert "超出本轮允许的控制块范围" in payload["correction"]
            assert "tests" not in progress.checkpoint
            plan = payload["single_edit_repair"]
            assert (plan["line_start"], plan["line_end"]) == (8, 11)
            replacement = "\n".join(SOURCE.splitlines()[7:11]).replace(
                "IF Ready THEN", partial["replacement"],
            )
            return schema(**receipt, edits=[dict(partial, line_end=11, replacement=replacement)])

    fixed, updated = await stage_call(
        Client(), request, "repair", STRepairStage, payload, "原阶段说明", progress,
        lambda value: apply_edits(SOURCE, bugs, value.edits),
    )
    assert "IF Ready AND Permit THEN" in fixed
    assert all(item.disposition == "fixed" for item in updated)
    assert len(calls) == int(whole_file)
    assert any(event.get("reused") for event in events) is not whole_file


@pytest.mark.asyncio
@pytest.mark.parametrize("extra,first_error,confirmed,triggered", [
    ("", OVERLAPPING_EDITS, True, True),
    ("", "当前阶段输出达到长度限制", True, False),
    ("", "补丁改变了 POU 名称", True, False),
    ("(*" + "x" * 4000 + "*)\n", OVERLAPPING_EDITS, True, False),
    ("(*" + "注" * 1400 + "*)\n", OVERLAPPING_EDITS, True, False),
    ("", OVERLAPPING_EDITS, False, False),
])
async def test_single_repair_fallback_is_only_for_short_overlapping_repairs(
    extra, first_error, confirmed, triggered,
):
    request = STStart(schema_version="1.0", code=SOURCE + extra, actions=["fix"])
    count = len(request.code.splitlines())
    pending = bug(1, 8, 11)
    if not confirmed:
        pending.confidence, pending.disposition = "possible", "needs_context"
    payload = {
        "repair_source_lines": [dict(line=i, text=text)
                                for i, text in enumerate(request.code.splitlines(), 1)],
        "bugs": [pending.model_dump()],
    }
    calls = []

    class Client:
        async def st_generate(self, schema, payload, instruction, request_id):
            calls.append(payload)
            assert ("single_edit_repair" in payload) is (triggered and len(calls) == 2)
            plan = payload.get("single_edit_repair", {"line_start": 1, "line_end": count})
            return schema(**payload["source_receipt"], edits=[dict(
                line_start=plan["line_start"], line_end=plan["line_end"], replacement=request.code,
                bug_ids=["B001"], explanation="测试恢复响应",
            )])

    def validate(value):
        if len(calls) == 1:
            raise DomainError("ST_OUTPUT_INVALID", first_error, 503)
        return value

    await stage_call(Client(), request, "repair", STRepairStage, payload, "原阶段说明",
                     lambda *args, **kwargs: None, validate)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_long_source_with_repeated_overlap_stops_after_two_attempts():
    request = STStart(
        schema_version="1.0", code=SOURCE + "(*" + "x" * 4000 + "*)\n", actions=["fix"],
    )
    payload = {
        "repair_source_lines": [dict(line=i, text=text)
                                for i, text in enumerate(request.code.splitlines(), 1)],
        "bugs": [bug(1, 8, 11).model_dump()],
    }
    calls = []

    class Client:
        async def st_generate(self, schema, payload, instruction, request_id):
            calls.append(payload)
            assert "single_edit_repair" not in payload
            raise DomainError("ST_OUTPUT_INVALID", OVERLAPPING_EDITS, 503)

    with pytest.raises(DomainError, match="两次结果未通过校验：补丁原始行范围重叠"):
        await stage_call(Client(), request, "repair", STRepairStage, payload, "原阶段说明",
                         lambda *args, **kwargs: None, lambda value: value)
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("model_output", ["block_only", "whole_file_text", "whole_file_range"])
async def test_single_block_preserves_unrelated_assignment_and_rejects_whole_file(model_output):
    source = SOURCE.replace("END_VAR\n", "    Other : INT;\nEND_VAR\n").replace(
        "END_PROGRAM\n", "Other := 7;\nEND_PROGRAM\n",
    )
    request = STStart(schema_version="1.0", code=source, actions=["fix"])
    bugs = [bug(1, 9, 12), bug(2, 9, 9)]
    payload = {
        "repair_source_lines": [dict(line=i, text=text)
                                for i, text in enumerate(source.splitlines(), 1)],
        "bugs": [item.model_dump() for item in bugs],
    }
    replacement = "IF Ready AND Permit THEN\nOutput := 8;\nValid := TRUE;\n"
    replacement += "ELSE\nOutput := 0;\nValid := FALSE;\nEND_IF;"
    full_file = "\n".join(source.splitlines()[:8]) + "\n" + replacement
    full_file += "\nOther := 999;\nEND_PROGRAM\n"
    calls = []

    class Client:
        async def st_generate(self, schema, payload, instruction, request_id):
            calls.append(payload)
            edit = dict(line_start=9, line_end=12, replacement=replacement,
                        bug_ids=["B001", "B002"], explanation="修复同一控制块")
            if len(calls) == 1:
                edits = [edit, {**edit, "line_end": 9}]
            else:
                plan = payload["single_edit_repair"]
                assert (plan["line_start"], plan["line_end"]) == (9, 12)
                assert [line["text"] for line in plan["source_lines"]] == source.splitlines()[8:12]
                assert "Other := 7;" not in "\n".join(line["text"] for line in plan["source_lines"])
                if model_output != "block_only":
                    edit["replacement"] = full_file
                if model_output == "whole_file_range":
                    edit.update(line_start=1, line_end=14)
                edits = [edit]
            return schema(**payload["source_receipt"], edits=edits)

    async def run():
        return await stage_call(
            Client(), request, "repair", STRepairStage, payload, "普通补丁说明",
            lambda *args, **kwargs: None, lambda value: apply_edits(source, bugs, value.edits),
        )

    if model_output == "block_only":
        fixed, updated = await run()
        assert fixed.startswith("\n".join(source.splitlines()[:8]) + "\n")
        assert fixed.endswith("\nOther := 7;\nEND_PROGRAM\n")
        assert all(item.disposition == "fixed" for item in updated)
        machine = Machine(fixed)
        machine.state.update(READY=True, PERMIT=True)
        machine.scan()
        assert machine.state["OUTPUT"] == 8 and machine.state["OTHER"] == 7
    else:
        with pytest.raises(DomainError, match="两次结果未通过校验"):
            await run()
    assert len(calls) == 2


def plan_for(source, ranges):
    return single_repair_plan({
        "repair_source_lines": [dict(line=i, text=text)
                                for i, text in enumerate(source.splitlines(), 1)],
        "bugs": [bug(i, start, end).model_dump()
                 for i, (start, end) in enumerate(ranges, 1)],
    }, len(source.splitlines()))


def test_region_uses_existing_comment_string_mask_and_handles_elsif():
    source = SOURCE.replace("    Valid : BOOL;", "    Valid : BOOL;\n    Label : STRING := 'IF';")
    source = source.replace("IF Ready THEN", "(* IF TRUE THEN (* END_FOR; *) END_CASE; *)\nIF Ready THEN")
    source = source.replace("END_IF;", "ELSIF Permit THEN\n    Valid := FALSE;\nEND_IF;")
    start = source.splitlines().index("IF Ready THEN") + 1
    end = source.splitlines().index("END_IF;") + 1
    plan = plan_for(source, [(start + 1, start + 1)])
    assert (plan["line_start"], plan["line_end"]) == (start, end)


def test_region_selects_smallest_safe_nested_block_and_rejects_siblings():
    source = """PROGRAM Gate
VAR b : BOOL; END_VAR
IF b THEN
    IF b THEN
        b := FALSE;
    END_IF;
    IF NOT b THEN
        b := TRUE;
    END_IF;
END_IF;
END_PROGRAM
"""
    plan = plan_for(source, [(5, 5)])
    assert (plan["line_start"], plan["line_end"]) == (4, 6)
    outer = plan_for(source, [(3, 10), (5, 5)])
    assert (outer["line_start"], outer["line_end"]) == (3, 10)
    assert plan_for(source, [(5, 5), (8, 8)]) is None


@pytest.mark.parametrize("source,ranges", [
    (SOURCE.replace("END_IF;", "END_FOR;"), [(8, 11)]),
    (SOURCE.replace("END_IF;", "END_IF; Output := 999;"), [(9, 9)]),
    (SOURCE.replace("IF Ready THEN", "Output := 999; IF Ready THEN"), [(9, 9)]),
    (SOURCE, [(3, 3)]),
    (SOURCE + SOURCE.replace("Gate", "OtherGate"), [(8, 11)]),
    (SOURCE.replace("END_PROGRAM\n", ""), [(8, 11)]),
])
def test_region_fails_closed_for_ambiguous_or_outside_scope_sources(source, ranges):
    assert plan_for(source, ranges) is None
