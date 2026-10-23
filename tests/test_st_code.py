import json
import time
from dataclasses import replace

import pytest
from pydantic import ValidationError

from apps.orchestrator.config import Settings
from apps.orchestrator.main import create_app
from packages.adapters.opencode import OpenCodeClient
from packages.adapters.st_code import (
    basic_st_issues,
    render_test_program,
    tokens,
    validate_result,
)
from packages.contracts.st_code import STStart, STTestCheck
from packages.domain.core import DomainError
from tests.auth_helpers import AuthenticatedClient as TestClient
from tests.fakes import FakeClient
from tests.st_fixtures import SOURCE, result_for
from tests.test_api import headers


def body(**changes):
    return dict(schema_version="1.0", code=SOURCE, **changes)


def wait(client, run_id):
    for _ in range(150):
        run = client.get("/api/v1/st-runs/" + run_id).json()
        if run["status"] not in ("queued", "running"):
            return run
        time.sleep(0.02)
    raise AssertionError(run)


def test_full_flow_exports_and_restart(tmp_path):
    app = create_app(Settings(state_dir=tmp_path), client_factory=FakeClient)
    with TestClient(app) as client:
        sent = client.post("/api/v1/st-runs", json=body(), headers=headers(client, "st-full"))
        assert sent.status_code == 202, sent.text
        run_id = sent.json()["run_id"]
        assert (
            client.post("/api/v1/st-runs", json=body(), headers=headers(client, "st-full")).json()[
                "run_id"
            ]
            == run_id
        )
        conflict = client.post(
            "/api/v1/st-runs", json=body(title="changed"), headers=headers(client, "st-full")
        )
        assert conflict.status_code == 409
        run = wait(client, run_id)
        assert run["status"] == "completed", run
        assert run["checks"]["annotation_tokens_preserved"] is True
        assert run["checks"]["compiler"] == run["checks"]["tests"] == "not_run"
        assert len(run["result"]["bugs"]) == 2
        downloads = {a["name"]: client.get(a["download_url"]) for a in run["artifacts"]}
        assert len(downloads) == 8
        assert all(r.status_code == 200 for r in downloads.values())
        assert downloads["original.st"].text == SOURCE
        assert "+Ratio := 0.0;" in downloads["changes.diff"].text
        assert "未在目标 PLC 执行" in downloads["report.md"].text
        assert downloads["result.json"].json()["source_sha256"] == run["source_sha256"]
        assert downloads["result.json"].json()["request"]["actions"] == [
            "comment",
            "test",
            "review",
            "fix",
        ]
        assert client.get("/api/v1/st-runs").json()["items"][0]["run_id"] == run_id
        assert client.get("/api/v1/runs").json()["items"] == []
        assert client.get("/api/v1/tuning-runs").json()["items"] == []
    with TestClient(
        create_app(Settings(state_dir=tmp_path), client_factory=FakeClient, worker_enabled=False)
    ) as client:
        persisted = client.get("/api/v1/st-runs/" + run_id).json()
        assert persisted["result"] == run["result"]
        assert client.get(persisted["artifacts"][0]["download_url"]).text == SOURCE


@pytest.mark.parametrize("actions", [["comment"], ["test"], ["review"], ["fix"], ["review", "fix"]])
def test_each_operation_is_independent(tmp_path, actions):
    with TestClient(create_app(Settings(state_dir=tmp_path), client_factory=FakeClient)) as client:
        sent = client.post("/api/v1/st-runs", json=body(actions=actions), headers=headers(client))
        run = wait(client, sent.json()["run_id"])
        assert run["status"] == "completed", run
        assert (run["result"]["annotated_code"] is not None) == ("comment" in actions)
        assert (run["result"]["test_code"] is not None) == ("test" in actions)
        assert (run["result"]["fixed_code"] is not None) == ("fix" in actions)


def test_owner_isolation_and_csrf(tmp_path):
    with TestClient(
        create_app(Settings(state_dir=tmp_path), client_factory=FakeClient, worker_enabled=False)
    ) as client:
        sent = client.post("/api/v1/st-runs", json=body(), headers=headers(client))
        run_id = sent.json()["run_id"]
        assert client.post("/api/v1/st-runs", json=body()).status_code == 403
        assert (
            client.post("/api/v1/st-runs", json=body(), headers=headers(client, "busy")).status_code
            == 409
        )
        platform = client.app.state.platform
        platform.create_user("other-user", "Other", "other-user-password")
        token, _, _ = platform.login("other-user", "other-user-password")
        client.cookies.set("office_session", token)
        assert client.get("/api/v1/st-runs").json()["items"] == []
        for path in [
            f"/api/v1/st-runs/{run_id}",
            f"/api/v1/st-runs/{run_id}/artifacts/original.st",
        ]:
            assert client.get(path).status_code == 404
        assert (
            client.post(
                f"/api/v1/st-runs/{run_id}/cancel", json={}, headers=headers(client)
            ).status_code
            == 404
        )
        client.cookies.clear()
        assert client.get("/api/v1/st-runs").status_code == 401


def test_cancel_and_restart_never_resubmit_uncertain_run(tmp_path):
    settings = Settings(state_dir=tmp_path)
    app = create_app(settings, client_factory=FakeClient, worker_enabled=False)
    with TestClient(app) as client:
        sent = client.post("/api/v1/st-runs", json=body(), headers=headers(client)).json()
        cancelled = client.post(
            f"/api/v1/st-runs/{sent['run_id']}/cancel", json={}, headers=headers(client)
        ).json()
        assert cancelled["status"] == "cancelled"
        sent = client.post("/api/v1/st-runs", json=body(), headers=headers(client, "again")).json()
        run = app.state.st_code.get(sent["run_id"])
        run.status, run.session_id = "running", "ses_uncertain"
        app.state.st_code.save(run)
    fake = FakeClient()
    with TestClient(create_app(settings, client_factory=lambda _: fake)) as client:
        run = client.get("/api/v1/st-runs/" + sent["run_id"]).json()
        assert run["status"] == "interrupted"
        assert not fake.calls


def test_cancel_running_task(tmp_path):
    fake = FakeClient(delay=10)
    with TestClient(
        create_app(Settings(state_dir=tmp_path), client_factory=lambda _: fake)
    ) as client:
        sent = client.post("/api/v1/st-runs", json=body(), headers=headers(client)).json()
        for _ in range(100):
            if client.get("/api/v1/st-runs/" + sent["run_id"]).json()["status"] == "running":
                break
            time.sleep(0.02)
        response = client.post(
            f"/api/v1/st-runs/{sent['run_id']}/cancel", json={}, headers=headers(client)
        )
        assert response.json()["status"] == "cancelled"
        assert wait(client, sent["run_id"])["result"] is None


def test_model_timeout_is_terminal_and_does_not_resubmit(tmp_path, monkeypatch):
    monkeypatch.delenv("ST_MODEL_TIMEOUT_SECONDS", raising=False)
    fake = FakeClient(delay=10)
    app = create_app(Settings(state_dir=tmp_path), client_factory=lambda _: fake)
    with TestClient(app) as client:
        assert app.state.st_code.model_timeout == 900
        app.state.st_code.model_timeout = 0.01
        run_id = client.post("/api/v1/st-runs", json=body(), headers=headers(client)).json()[
            "run_id"
        ]
        run = wait(client, run_id)
        assert run["status"] == "failed" and "ST_MODEL_TIMEOUT" in run["error"]
        assert run["result"] is None and run["attempt_count"] == 1
        assert len(fake.calls) == 1


@pytest.mark.parametrize(
    "changes",
    [
        dict(code=" "),
        dict(code="\x00bad"),
        dict(code="a" * 12001),
        dict(code="x\n" * 601),
        dict(actions=[]),
        dict(actions=["comment", "comment"]),
        dict(filename="../bad.st"),
        dict(filename="bad.py"),
        dict(dialect="invented"),
    ],
)
def test_request_limits(changes):
    with pytest.raises(ValidationError):
        STStart.model_validate({**body(), **changes})


@pytest.mark.parametrize(
    "source",
    [
        "x := '(* literal *)'; // tail\ny := '//';",
        "x := 'can''t'; y := 'a$'b'; (* nested (* inner *) *)",
        "{attribute 'keep'}\nx := 1.0e-3; y := T#100ms;",
        "a := b + +c; z := b ** 2;",
    ],
)
def test_annotation_checks_strings_pragmas_and_exact_tokens(source):
    assert tokens(source) == tokens("(* new explanation *)\n" + source)
    request = STStart(schema_version="1.0", code=source, actions=["comment"])
    result = result_for(request)
    result.annotated_code = (
        result.annotated_code.replace("x :=", "x : =")
        if "x :=" in source
        else result.annotated_code + "\nz := 0;"
    )
    with pytest.raises(DomainError, match="有效词元"):
        validate_result(request, result)


@pytest.mark.parametrize(
    "mutation",
    ["logic", "string", "pragma", "line", "evidence", "uncertain_fix", "regression", "unclosed"],
)
def test_reject_untrustworthy_output(mutation):
    request = STStart(**body())
    result = result_for(request)
    if mutation == "logic":
        result.annotated_code = result.annotated_code.replace(" / ", " * ")
    elif mutation == "string":
        request.code += "\nx := 'a';"
        result.annotated_code += "\nx := 'b';"
    elif mutation == "pragma":
        request.code += "\n{attribute 'keep'}"
        result.annotated_code += "\n{attribute 'remove'}"
    elif mutation == "line":
        result.bugs[0].line_end = 999
    elif mutation == "evidence":
        result.bugs[0].evidence = "fabricated evidence"
    elif mutation == "uncertain_fix":
        result.bugs[0].confidence = "possible"
    elif mutation == "regression":
        result.tests[-1].related_bug_ids = []
    elif mutation == "unclosed":
        result.fixed_code += "\n(*"
    with pytest.raises(DomainError):
        validate_result(request, result)


@pytest.mark.parametrize(
    "source,changed",
    [
        ("压力 := 1;", "压(* 注释 *)力 := 1;"),
        ("x := 16#FF;", "x := 16(* 注释 *)#FF;"),
        ("x := DINT#16#FF;", "x := DINT#16(* 注释 *)#FF;"),
        ("x := T#100ms;", "x := T#100(* 注释 *)ms;"),
        ("x := DT#2026-10-20-10:00:00;", "x := DT#2026(* 注释 *)-10-20-10:00:00;"),
        ("x := STRING#'abc';", "x := STRING(* 注释 *)#'abc';"),
        ("x := 1_000;", "x := 1(* 注释 *)_000;"),
        ("a : ARRAY[1..3] OF INT;", "a : ARRAY[1.(* 注释 *).3] OF INT;"),
    ],
)
def test_annotation_cannot_split_st_literals_or_identifiers(source, changed):
    request = STStart(schema_version="1.0", code=source, actions=["comment"])
    result = result_for(request)
    result.annotated_code = changed
    with pytest.raises(DomainError, match="有效词元"):
        validate_result(request, result)


@pytest.mark.parametrize(
    "mutation", ["end_if", "declaration", "placeholder", "helper", "undeclared", "assertion_type"]
)
def test_reject_incomplete_generated_st(mutation):
    request = STStart(**body())
    result = result_for(request)
    if mutation == "end_if":
        result.fixed_code = result.fixed_code.replace("END_IF;", "END_IF")
    elif mutation == "declaration":
        result.test_code = result.test_code.replace("TestPassed : BOOL;", "TestPassed BOOL;")
    elif mutation == "placeholder":
        result.test_code += "\n..."
    elif mutation == "helper":
        result.test_code = result.test_code.replace("(Sut.Ratio = 5.0)", "RM(Sut.Ratio, 5.0)")
    elif mutation == "undeclared":
        result.test_code = result.test_code.replace("TestPassed :=", "AllPassed :=")
    else:
        result.test_code = result.test_code.replace("TestPassed : BOOL;", "TestPassed : REAL;")
    with pytest.raises(DomainError):
        validate_result(request, result)


def test_generated_harness_owns_declarations_and_initializes_sequential_assertions():
    request = STStart(
        schema_version="1.0",
        code="FUNCTION SafeRatio : REAL\nVAR_INPUT n : REAL; d : REAL; END_VAR\nSafeRatio := n / d;\nEND_FUNCTION",
    )
    checks = [
        STTestCheck(
            test_id="T001", actual="SafeRatio(n := 6.0, d := 3.0)", expected="2.0", tolerance=0.001
        ),
        STTestCheck(
            test_id="T001", actual="SafeRatio(n := 5.0, d := 0.0)", expected="0.0", tolerance=0.001
        ),
    ]
    program = render_test_program(request, [{"id": "T001"}], [], checks)
    assert program.count("T001 := TRUE;") == 1
    assert program.count("T001 := T001 AND") == 2
    assert "AllPassed : BOOL;" in program
    assert basic_st_issues(program, request.code, test=True) == []
    assert any(
        "缺少输入参数" in issue
        for issue in basic_st_issues(
            program.replace("SafeRatio(n := 6.0, d := 3.0)", "SafeRatio()"), request.code, test=True
        )
    )
    checks[0].setup = "T001 := FALSE;"
    with pytest.raises(DomainError, match="断言变量"):
        render_test_program(request, [{"id": "T001"}], [], checks)


def test_one_correction_only_and_no_retry_on_unknown(tmp_path):
    class InvalidClient(FakeClient):
        async def st_review(self, request, on_session, correction=""):
            self.calls.append(("st-code", correction))
            result = result_for(request)
            result.annotated_code += "\ninvalid := 1;"
            return result

    fake = InvalidClient()
    with TestClient(
        create_app(Settings(state_dir=tmp_path), client_factory=lambda _: fake)
    ) as client:
        run_id = client.post("/api/v1/st-runs", json=body(), headers=headers(client)).json()[
            "run_id"
        ]
        run = wait(client, run_id)
        assert run["status"] == "failed" and run["result"] is None
        assert len(fake.calls) == 2 and fake.calls[1][1]
        assert run["attempt_count"] == 2 and len(run["validation_feedback"]) == 1

    class UnknownClient(FakeClient):
        async def st_review(self, request, on_session, correction=""):
            self.calls.append(("st-code", correction))
            raise DomainError("RESULT_UNKNOWN")

    fake = UnknownClient()
    with TestClient(
        create_app(replace(Settings(state_dir=tmp_path)), client_factory=lambda _: fake)
    ) as client:
        run_id = client.post(
            "/api/v1/st-runs", json=body(), headers=headers(client, "unknown")
        ).json()["run_id"]
        assert wait(client, run_id)["status"] == "failed"
        assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_opencode_adapter_permissions_and_typed_stages():
    request = STStart(**body())
    expected = result_for(request)
    client = OpenCodeClient(Settings(provider="local", model="test"))
    client.ready = True
    sent, progress = [], []

    async def api(method, path, **kwargs):
        assert path == "/agent"
        return [
            dict(
                name="st-code",
                permission=[
                    dict(permission="*", pattern="*", action="allow"),
                    dict(permission="*", pattern="*", action="deny"),
                    dict(
                        permission="external_directory",
                        pattern="/tmp/tool-output/*",
                        action="allow",
                    ),
                ],
            )
        ]

    async def generate(schema, payload, instruction, request_id):
        sent.append((schema, payload, instruction, request_id))
        values = {
            "review": dict(
                summary=expected.summary,
                findings=[
                    {
                        k: v
                        for k, v in bug.model_dump().items()
                        if k
                        in {
                            "title",
                            "severity",
                            "confidence",
                            "line_start",
                            "line_end",
                            "explanation",
                            "suggestion",
                        }
                    }
                    for bug in expected.bugs
                ],
                assumptions=[],
            ),
            "annotation": dict(comments=[dict(line=1, text="只添加注释")]),
            "repair": dict(
                edits=[
                    dict(
                        line_start=12,
                        line_end=12,
                        replacement="Ratio := 0.0;\nValid := FALSE;\nIF Enable AND (Denominator <> 0.0) THEN",
                        bug_ids=["B001", "B002"],
                        explanation="防除零和状态保持",
                    )
                ]
            ),
            "tests": dict(
                scenarios=[
                    dict(
                        id="T001",
                        title="比值及禁用回归",
                        category="regression",
                        related_bug_ids=["B001", "B002"],
                        execution="st",
                        scans=[
                            dict(
                                assignments=[
                                    dict(target="Enable", value="TRUE"),
                                    dict(target="Numerator", value="10.0"),
                                    dict(target="Denominator", value="2.0"),
                                ],
                                assertions=[dict(target="Ratio", expected="5.0", tolerance=0.001)],
                            ),
                            dict(
                                assignments=[dict(target="Enable", value="FALSE")],
                                assertions=[dict(target="Ratio", expected="0.0", tolerance=0.001)],
                            ),
                        ],
                        manual_preconditions="",
                        manual_steps=[],
                        manual_expected="",
                        adaptation_note="",
                    )
                ]
            ),
        }
        return schema.model_validate(dict(**payload["source_receipt"], **values[payload["stage"]]))

    client.api, client.st_generate = api, generate
    reviewed = await client.st_review(request, lambda session, **value: progress.append(value))
    assert tokens(reviewed.annotated_code) == tokens(request.code)
    assert "AllPassed : BOOL;" in reviewed.test_code
    assert "T001 := TRUE;" in reviewed.test_code
    assert len(sent) == 4
    assert [value["stage"] for value in progress if "request_id" in value] == [
        "review",
        "annotation",
        "repair",
        "tests",
    ]
    assert len([value for value in progress if "output" in value]) == 4
    assert sent[-1][0].model_json_schema()["properties"]["scenarios"]["type"] == "array"
    assert sent[0][1]["source_lines"][12] == {
        "line": 13,
        "text": "    Ratio := Numerator / Denominator;",
    }
    assert reviewed.bugs[0].evidence == "    Ratio := Numerator / Denominator;"
    await client.close()


def test_line_comments_preserve_exact_source_and_reject_comment_injection():
    from packages.adapters.st_workflow import add_comments
    from packages.contracts.st_code import STLineComment

    code = "x := 1;\r\ny := 'a';"
    result = add_comments(code, [STLineComment(line=2, text="说明当前赋值")])
    assert result == "x := 1;\r\n(* 说明当前赋值 *)\ny := 'a';"
    assert tokens(result) == tokens(code)
    for text in ["坏注释 *) x:=2;", "换\n行"]:
        with pytest.raises(DomainError):
            add_comments(code, [STLineComment(line=1, text=text)])
    with pytest.raises(DomainError):
        add_comments(code, [STLineComment(line=3, text="越界")])


def test_patches_use_original_lines_and_protect_interface():
    from packages.adapters.st_workflow import apply_edits
    from packages.contracts.st_code import STSourceEdit

    request = STStart(**body())
    bugs = result_for(request).bugs
    edit = STSourceEdit(
        line_start=12,
        line_end=12,
        replacement="Ratio := 0.0;\nValid := FALSE;\nIF Enable AND (Denominator <> 0.0) THEN",
        bug_ids=["B001", "B002"],
        explanation="限定缺陷修复",
    )
    fixed, updated = apply_edits(SOURCE, bugs, [edit])
    assert fixed == result_for(request).fixed_code
    assert all(b.disposition == "fixed" for b in updated)
    assert all(b.disposition == "fixed" for b in bugs)  # original result fixture remains unchanged
    for changes in [
        dict(line_start=1, line_end=1, replacement="FUNCTION_BLOCK Renamed"),
        dict(bug_ids=["B999"]),
        dict(line_start=100, line_end=100),
        dict(replacement="IF Enable THEN"),
    ]:
        with pytest.raises(DomainError):
            apply_edits(SOURCE, bugs, [edit.model_copy(update=changes)])
    with pytest.raises(DomainError, match="重叠"):
        apply_edits(SOURCE, bugs, [edit, edit])
    possible = [b.model_copy(update={"confidence": "possible"}) for b in bugs]
    with pytest.raises(DomainError, match="已确认"):
        apply_edits(SOURCE, possible, [edit])


def test_program_tests_wrap_production_body_and_require_real_scan():
    from packages.adapters.st_workflow import program_test_subject
    from packages.contracts.st_code import STTestDeclaration

    code = "PROGRAM PLC_PRG\nVAR b : BOOL; END_VAR\nb := NOT b;\nEND_PROGRAM\n"
    request = STStart(schema_version="1.0", code=code, actions=["test"])
    subject = program_test_subject(code)
    assert (
        subject
        == "FUNCTION_BLOCK ST_TestSubject\nVAR b : BOOL; END_VAR\nb := NOT b;\nEND_FUNCTION_BLOCK\n"
    )
    declaration = STTestDeclaration(name="Sut", type="ST_TestSubject")
    check = STTestCheck(
        test_id="T001", setup="Sut.b := FALSE; Sut();", actual="Sut.b", expected="TRUE"
    )
    program = render_test_program(
        request, [dict(id="T001")], [declaration], [check], subject=subject
    )
    assert "Sut();" in program and "PLC_PRG" not in program
    assert basic_st_issues(program, code, test=True) == []
    with pytest.raises(DomainError, match="没有调用"):
        render_test_program(
            request,
            [dict(id="T001")],
            [declaration],
            [check.model_copy(update={"setup": "Sut.b := FALSE;"})],
            subject=subject,
        )
    with pytest.raises(DomainError, match="PROGRAM"):
        render_test_program(
            request,
            [dict(id="T001")],
            [STTestDeclaration(name="Sut", type="PLC_PRG")],
            [check],
            subject=subject,
        )
    with pytest.raises(DomainError, match="没有读取"):
        render_test_program(
            request,
            [dict(id="T001")],
            [declaration],
            [check.model_copy(update={"actual": "OtherTimer.Q"})],
            subject=subject,
        )


def test_manual_timer_tests_are_not_claimed_as_st_checks():
    request = STStart(schema_version="1.0", code=SOURCE, actions=["test"])
    program = render_test_program(
        request, [dict(id="T001", execution="manual", adaptation_note="目标PLC累计20秒")], [], []
    )
    assert "AllPassed := FALSE;" in program
    assert "T001 人工/PLC时钟用例" in program
    assert "T001 : BOOL" not in program
    with pytest.raises(DomainError):
        render_test_program(
            request,
            [dict(id="T001", execution="manual")],
            [],
            [STTestCheck(test_id="T001", actual="TRUE", expected="TRUE")],
        )


def test_batch_mixer_regression_exposes_top_level_and_conditional_timer_calls():
    from pathlib import Path

    from packages.adapters.st_workflow import call_contexts, program_test_subject

    code = (Path(__file__).parent / "fixtures/st/batch_mixer.st").read_text()
    contexts = call_contexts(code)
    assert contexts[0]["instance"] == "tMix" and contexts[0]["conditional_scopes"] == []
    assert contexts[1]["instance"] == "tOut" and contexts[1]["conditional_scopes"] == [
        "CASE eStep OF"
    ]
    assert any("分号" in issue for issue in basic_st_issues(code))
    subject = program_test_subject(code)
    assert subject.endswith("END_FUNCTION_BLOCK\n")
    assert "bStartOld  := bStart;" in subject


@pytest.mark.asyncio
async def test_stage_correction_repeats_only_completed_invalid_stage_and_keeps_results():
    from packages.adapters.st_workflow import add_comments, stage_call
    from packages.contracts.st_code import STAnnotationStage

    client = OpenCodeClient(Settings(provider="local", model="test"))
    sent, progress = [], []

    async def generate(schema, payload, instruction, request_id):
        sent.append(payload)
        return schema.model_validate(
            dict(
                **payload["source_receipt"],
                comments=[{"line": 1, "text": "第一次坏 *)" if len(sent) == 1 else "正确说明"}],
            )
        )

    client.st_generate = generate
    result = await stage_call(
        client,
        STStart(**body()),
        "annotation",
        STAnnotationStage,
        {},
        "",
        lambda session, **value: progress.append(value),
        lambda value: add_comments(SOURCE, value.comments),
    )
    assert result == "(* 正确说明 *)\n" + SOURCE
    assert len(sent) == 2 and sent[-1]["correction"]
    assert "previous_output" not in sent[0]
    assert sent[-1]["previous_output"]["comments"][0]["text"] == "第一次坏 *)"
    starts = [value for value in progress if "request_id" in value]
    assert len(starts) == 2 and starts[-1]["attempt"] == 2 and starts[-1]["feedback"]
    assert progress[-1]["output"]["comments"][0]["text"] == "正确说明"
    await client.close()


@pytest.mark.asyncio
async def test_stage_rejects_incomplete_source_receipt():
    from packages.adapters.st_workflow import stage_call
    from packages.contracts.st_code import STAnnotationStage

    client = OpenCodeClient(Settings(provider="local", model="test"))
    sent = []

    async def generate(schema, payload, instruction, request_id):
        sent.append(payload)
        return schema(
            source_line_count=5,
            source_last_line="incomplete",
            comments=[{"line": 1, "text": "说明"}],
        )

    client.st_generate = generate
    with pytest.raises(DomainError, match="核对原始代码"):
        await stage_call(
            client,
            STStart(**body()),
            "annotation",
            STAnnotationStage,
            {},
            "",
            lambda session, **value: None,
            lambda value: value,
        )
    assert len(sent) == 2
    await client.close()


def test_cyclic_test_cannot_bypass_subject_with_trivial_constant_assertion():
    from packages.adapters.st_workflow import program_test_subject

    code = "PROGRAM P\nVAR b:BOOL;END_VAR\nb:=TRUE;\nEND_PROGRAM"
    request = STStart(schema_version="1.0", code=code, actions=["test"])
    check = STTestCheck(test_id="T001", actual="TRUE", expected="TRUE")
    with pytest.raises(DomainError, match="未声明被测"):
        render_test_program(
            request, [dict(id="T001")], [], [check], subject=program_test_subject(code)
        )
    request = STStart(
        schema_version="1.0",
        code="FUNCTION F:BOOL\nVAR_INPUT b:BOOL;END_VAR\nF:=b;\nEND_FUNCTION",
        actions=["test"],
    )
    with pytest.raises(DomainError, match="没有调用被测函数"):
        render_test_program(request, [dict(id="T001")], [], [check])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode,expected_code",
    [
        ("valid", None),
        ("length", "ST_OUTPUT_INVALID"),
        ("malformed", "ST_OUTPUT_INVALID"),
        ("timeout", "ST_RESULT_UNKNOWN"),
        ("status", "ST_MODEL_PROTOCOL_ERROR"),
    ],
)
async def test_native_schema_request_and_unknown_outcomes_never_repost(
    tmp_path, monkeypatch, mode, expected_code
):
    import httpx

    from packages.adapters import st_model
    from packages.contracts.st_code import STAnnotationStage

    config = tmp_path / "provider.json"
    config.write_text(
        json.dumps(
            {
                "provider": {
                    "local": {
                        "options": {
                            "baseURL": "http://model.invalid/v1",
                            "apiKey": "{env:ST_TEST_ONLY_KEY}",
                        }
                    }
                }
            }
        )
    )
    monkeypatch.setenv("DEMO_PROVIDER_CONFIG", str(config))
    monkeypatch.setenv("ST_TEST_ONLY_KEY", "test-only-no-real-secret")
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        assert request.headers["X-Request-ID"] == "st-request-test"
        if mode == "timeout":
            raise httpx.ReadTimeout("unknown send outcome", request=request)
        if mode == "status":
            return httpx.Response(400, json={"error": "unsupported schema"})
        content = (
            json.dumps(
                {
                    "source_line_count": 16,
                    "source_last_line": "END_FUNCTION_BLOCK",
                    "comments": [{"line": 1, "text": "说明"}],
                }
            )
            if mode != "malformed"
            else "bad"
        )
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "finish_reason": "length" if mode == "length" else "stop",
                        "message": {"content": content},
                    }
                ]
            },
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        st_model.httpx,
        "AsyncClient",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler)),
    )
    if expected_code:
        with pytest.raises(DomainError) as error:
            await st_model.generate(
                Settings(provider="local", model="test"),
                STAnnotationStage,
                {},
                "说明",
                "st-request-test",
            )
        assert error.value.code == expected_code
    else:
        result = await st_model.generate(
            Settings(provider="local", model="test"),
            STAnnotationStage,
            {},
            "说明",
            "st-request-test",
        )
        assert result.comments[0].text == "说明"
    assert len(sent) == 1
    assert "tools" not in sent[0] and "tool_choice" not in sent[0]
    assert sent[0]["response_format"]["json_schema"]["strict"] is True
    assert (
        sent[0]["response_format"]["json_schema"]["schema"]["properties"]["comments"]["type"]
        == "array"
    )


def test_static_baseline_finds_dead_edge_and_groups_all_missing_terminators():
    from pathlib import Path

    from packages.adapters.st_workflow import static_findings

    code = (Path(__file__).parent / "fixtures/st/batch_mixer.st").read_text()
    findings = static_findings(code)
    assert len(findings) == 2
    assert (findings[0].line_start, findings[0].line_end) == (22, 23)
    assert "FALSE" in findings[0].explanation
    for line in (30, 41, 49, 56, 65, 66, 70):
        assert str(line) in findings[1].explanation
    assert not static_findings(
        code.replace(
            "bStartOld  := bStart;\nbStartEdge := bStart AND NOT bStartOld;",
            "bStartEdge := bStart AND NOT bStartOld;\nbStartOld  := bStart;",
        )
        .replace("END_FOR\n", "END_FOR;\n")
        .replace("END_IF\n", "END_IF;\n")
        .replace("END_CASE\n", "END_CASE;\n")
    )
    # A deliberately false expression without edge intent is not auto-repaired.
    assert not static_findings("a := b;\nc := b AND NOT a;")


@pytest.mark.asyncio
async def test_annotation_uses_proven_data_flow_instead_of_false_edge_description():
    from pathlib import Path

    from packages.adapters.st_workflow import run_workflow

    code = (Path(__file__).parent / "fixtures/st/batch_mixer.st").read_text()
    request = STStart(schema_version="1.0", code=code, actions=["comment"])

    class Model:
        async def st_generate(self, schema, payload, instruction, request_id):
            return schema.model_validate(
                dict(
                    **payload["source_receipt"],
                    comments=[
                        {"line": 22, "text": "错误说明：保存上一扫描值"},
                        {"line": 23, "text": "错误说明：成功计算上升沿"},
                    ],
                )
            )

    value = await run_workflow(Model(), request, lambda session, **progress: None)
    assert "错误说明" not in value.annotated_code
    assert "结果恒为 FALSE" in value.annotated_code
    assert tokens(value.annotated_code) == tokens(code)


@pytest.mark.asyncio
async def test_annotation_rejects_invented_flow_units_without_engineering_context():
    from pathlib import Path

    from packages.adapters.st_workflow import run_workflow

    code = (Path(__file__).parent / "fixtures/st/batch_mixer.st").read_text()
    request = STStart(schema_version="1.0", code=code, actions=["comment"])
    calls = []

    class Model:
        async def st_generate(self, schema, payload, instruction, request_id):
            calls.append(payload)
            return schema.model_validate(
                dict(**payload["source_receipt"], comments=[{"line": 45, "text": "每秒累计流量"}])
            )

    with pytest.raises(DomainError, match="工程未提供"):
        await run_workflow(Model(), request, lambda session, **progress: None)
    assert len(calls) == 2


def test_static_checks_ignore_comments_strings_and_pragmas_without_changing_line_offsets():
    from packages.adapters.st_workflow import call_contexts, code_mask, static_findings

    code = "(* nested (* a *)\nxOld := x;\nxEdge := x AND NOT xOld;\nEND_IF\n*)\ns := 'END_IF';\n{attribute 'END_IF'}\n// t : TON;\n// t(IN:=TRUE);\n"
    assert static_findings(code) == [] and call_contexts(code) == []
    assert len(code_mask(code)) == len(code)
    assert code_mask(code).count("\n") == code.count("\n")
    with_real = (
        code
        + "bOld := b; (* history written too soon *)\nbEdge := b AND NOT bOld;\nEND_IF (* missing terminator *)\n"
    )
    findings = static_findings(with_real)
    assert len(findings) == 2 and findings[0].line_start == 10 and findings[1].line_start == 12


def test_stop_control_policy_stays_pending_and_correct_timer_is_not_a_defect():
    from pathlib import Path

    from packages.adapters.st_workflow import review_policy, stop_blocks, stop_policy_provided
    from packages.contracts.st_code import STFinding

    code = (Path(__file__).parent / "fixtures/st/batch_mixer.st").read_text()
    request = STStart(schema_version="1.0", code=code)
    findings = [
        STFinding(
            title="停止输出延迟",
            severity="medium",
            confidence="confirmed",
            line_start=68,
            line_end=70,
            explanation="输出下一扫描才复位",
            suggestion="立即复位输出",
        ),
        STFinding(
            title="tMix无需复位",
            severity="medium",
            confidence="confirmed",
            line_start=25,
            line_end=25,
            explanation="每扫描IN=FALSE自动复位，此用法正确。但又猜测其他输出保持。",
            suggestion="在其他状态加入不必要的输出复位。",
        ),
    ]
    result = review_policy(request, findings)
    assert len(result) == 1 and result[0].confidence == "possible"
    assert "确认" in result[0].suggestion
    assert findings[0].confidence == "confirmed"
    assert stop_blocks(code)[0]["line_start"] == 68
    assert (
        stop_blocks(code)[0]["tokens"]
        == stop_blocks(code.replace("END_IF\n", "END_IF;\n"))[0]["tokens"]
    )
    assert not stop_policy_provided(request)
    specified = request.model_copy(update={"instructions": "bStop要求立即关闭输出"})
    assert stop_policy_provided(specified)
    assert review_policy(specified, findings)[0].confidence == "confirmed"


@pytest.mark.parametrize(
    "explanation",
    [
        "tMix每扫描调用，IN=(eStep=2)。当eStep从2变为3时，IN变为FALSE，"
        "tMix自动复位Q和ET，此行为正确。但若eStep在2和3之间快速切换，tMix可能未完全计时。",
        "IN变为FALSE时自动清零Q与ET，快速状态切换是否允许需工程确认。",
    ],
)
def test_fresh_model_contradictory_timer_retention_finding_does_not_require_fake_fix(explanation):
    from pathlib import Path

    from packages.adapters.st_workflow import review_policy
    from packages.contracts.st_code import STFinding

    code = (Path(__file__).parent / "fixtures/st/batch_mixer.st").read_text()
    request = STStart(schema_version="1.0", code=code)
    finding = STFinding(
        title="tMix TON在eStep=2时IN为TRUE，离开后状态保持",
        severity="medium", confidence="confirmed", line_start=25, line_end=25,
        explanation=explanation,
        suggestion="确认混合持续时间是否足够长，若存在快速切换风险，需增加状态保持逻辑。",
    )
    assert review_policy(request, [finding]) == []
    # This policy must not suppress the actual gated timer defect in CASE.
    gated = finding.model_copy(update={"line_start": 60, "line_end": 60})
    assert review_policy(request, [gated])[0].confidence == "confirmed"


def test_platform_baseline_repairs_preserve_line_count_and_do_not_change_stop_policy():
    from pathlib import Path

    from packages.adapters.st_workflow import repair_baseline, static_findings, stop_blocks

    code = (Path(__file__).parent / "fixtures/st/batch_mixer.st").read_text()
    fixed = repair_baseline(code)
    assert len(fixed.splitlines()) == len(code.splitlines())
    assert fixed.splitlines()[21] == "bStartEdge := bStart AND NOT bStartOld;"
    assert fixed.splitlines()[22] == "bStartOld  := bStart;"
    assert not static_findings(fixed)
    assert stop_blocks(code)[0]["tokens"] == stop_blocks(fixed)[0]["tokens"]
    snippet = "bOld:=b;\r\nbEdge:=b AND NOT bOld;"
    assert repair_baseline(snippet) == "bEdge:=b AND NOT bOld;\r\nbOld:=b;"
    assert repair_baseline("END_IF (* keep comment *)\n") == "END_IF; (* keep comment *)\n"


def test_missing_regression_feedback_identifies_exact_bug_for_correction():
    request = STStart(**body())
    result = result_for(request)
    for case in result.tests:
        case.related_bug_ids = [value for value in case.related_bug_ids if value != "B002"]
    with pytest.raises(DomainError, match="B002"):
        validate_result(request, result)


def test_retry_preserves_inputs_enforces_owner_and_is_idempotent(tmp_path):
    app = create_app(Settings(state_dir=tmp_path), client_factory=FakeClient, worker_enabled=False)
    with TestClient(app) as client:
        first = client.post(
            "/api/v1/st-runs", json=body(), headers=headers(client, "retry-original")
        ).json()
        run = app.state.st_code.get(first["run_id"])
        run.status = "failed"
        app.state.st_code.save(run)
        retry = body(retry_from=run.run_id)
        response = client.post(
            "/api/v1/st-runs", json=retry, headers=headers(client, "retry-actual")
        )
        assert response.status_code == 202, response.text
        new = response.json()
        assert new["retry_of"] == run.run_id and new["request"]["retry_from"] is None
        assert new["source_sha256"] == run.source_sha256
        assert (
            client.post(
                "/api/v1/st-runs", json=retry, headers=headers(client, "retry-actual")
            ).json()["run_id"]
            == new["run_id"]
        )
        client.post(
            "/api/v1/st-runs/" + new["run_id"] + "/cancel",
            json={},
            headers=headers(client, "cancel-retry"),
        )
        invalid = client.post(
            "/api/v1/st-runs",
            json=body(retry_from=run.run_id, title="different"),
            headers=headers(client, "retry-mismatch"),
        )
        assert invalid.status_code == 409 and invalid.json()["error"]["code"] == "ST_RETRY_INVALID"
        platform = app.state.platform
        platform.create_user("retry-other", "Other", "Other-password-123")
        token, _, _ = platform.login("retry-other", "Other-password-123")
        client.cookies.set("office_session", token)
        assert (
            client.post(
                "/api/v1/st-runs", json=retry, headers=headers(client, "retry-other")
            ).status_code
            == 404
        )


@pytest.mark.asyncio
async def test_retry_revalidates_cached_stage_and_reuses_only_valid_data():
    from packages.adapters.st_workflow import add_comments, stage_call
    from packages.contracts.st_code import STAnnotationStage

    client = OpenCodeClient(Settings(provider="local", model="test"))
    request = STStart(**body())
    receipt = dict(
        source_line_count=len(SOURCE.splitlines()), source_last_line=SOURCE.splitlines()[-1]
    )
    events = []

    def progress(session, **value):
        events.append(value)

    progress.checkpoint = {"annotation": dict(**receipt, comments=[dict(line=1, text="缓存说明")])}

    async def forbidden(*args):
        raise AssertionError("valid cached stage must not infer again")

    client.st_generate = forbidden
    result = await stage_call(
        client,
        request,
        "annotation",
        STAnnotationStage,
        {},
        "",
        progress,
        lambda value: add_comments(SOURCE, value.comments),
    )
    assert result.startswith("(* 缓存说明 *)") and events[0]["reused"] is True
    calls = []
    progress.checkpoint = {
        "annotation": dict(**receipt, comments=[dict(line=1, text="坏 *)")]),
        "repair": {"stale": True},
    }

    async def correct(schema, payload, instruction, request_id):
        calls.append(payload)
        return schema.model_validate(dict(**receipt, comments=[dict(line=1, text="新说明")]))

    client.st_generate = correct
    result = await stage_call(
        client,
        request,
        "annotation",
        STAnnotationStage,
        {},
        "",
        progress,
        lambda value: add_comments(SOURCE, value.comments),
    )
    assert result.startswith("(* 新说明 *)") and len(calls) == 1
    assert "校验未通过" in calls[0]["correction"] and "repair" not in progress.checkpoint
    await client.close()
