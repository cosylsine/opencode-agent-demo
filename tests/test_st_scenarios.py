import pytest

from packages.adapters.st_code import basic_st_issues
from packages.adapters.st_scenarios import render_scenarios
from packages.adapters.st_workflow import program_test_subject
from packages.contracts.st_code import STBug, STResult, STScenariosStage, STStart
from packages.domain.core import DomainError

SOURCE = """PROGRAM PLC_PRG
VAR
    b : BOOL;
    r : REAL;
    a : ARRAY[1..2] OF REAL;
    t : TON;
END_VAR
r := r + 1.0;
t(IN := b, PT := T#1S);
END_PROGRAM
"""


def scenario(id="T001", **changes):
    value = dict(
        id=id,
        title="独立扫描检查",
        category="normal",
        related_bug_ids=[],
        execution="st",
        scans=[
            dict(
                assignments=[dict(target="r", value="0.0")],
                assertions=[
                    dict(target="r", expected="1.0", tolerance=0.001),
                    dict(target="b", expected="FALSE", tolerance=None),
                ],
            )
        ],
        manual_preconditions="",
        manual_steps=[],
        manual_expected="",
        adaptation_note="",
    )
    return dict(value, **changes)


def render(cases, bugs=()):
    request = STStart(schema_version="1.0", code=SOURCE)
    result = STResult(
        summary="测试",
        annotated_code=None,
        test_code=None,
        tests=[],
        bugs=list(bugs),
        fixed_code=SOURCE,
        assumptions=[],
    )
    plan = STScenariosStage(
        source_line_count=len(SOURCE.splitlines()), source_last_line="END_PROGRAM", scenarios=cases
    )
    return render_scenarios(request, result, plan, program_test_subject(SOURCE))


def test_one_call_per_scan_isolated_instances_and_documentation_share_constants():
    cases = [
        scenario(manual_expected="错误的另一套预期值20，不应显示"),
        scenario(
            "T002",
            scans=[
                dict(
                    assignments=[dict(target="r", value="10.0")],
                    assertions=[dict(target="r", expected="11.0", tolerance=0.01)],
                )
            ],
        ),
    ]
    tests, code = render(cases)
    assert code.count("Sut_T001();") == 1 and code.count("Sut_T002();") == 1
    assert "Sut_T001 : ST_TestSubject;" in code and "Sut_T002 : ST_TestSubject;" in code
    assert "IF NOT Executed THEN" in code and "Executed := TRUE;" in code
    assert "另一套" not in tests[0].expected
    assert "r = 1.0" in tests[0].expected and "r = 11.0" in tests[1].expected
    assert "r := 10.0" in tests[1].steps[0]
    assert basic_st_issues(code, SOURCE, test=True) == []


def test_continuous_scan_preserves_only_same_case_instance():
    scans = scenario()["scans"] + [
        dict(assignments=[], assertions=[dict(target="r", expected="2.0", tolerance=0.001)])
    ]
    tests, code = render([scenario(scans=scans)])
    assert code.count("Sut_T001();") == 2
    assert "扫描 2：r = 2.0" in tests[0].expected


@pytest.mark.parametrize(
    "assignment",
    [
        dict(target="unknown", value="1.0"),
        dict(target="a[3]", value="1.0"),
        dict(target="a", value="1.0"),
        dict(target="t.Q", value="TRUE"),
        dict(target="t", value="TRUE"),
        dict(target="r", value="TRUE"),
        dict(target="b", value="1"),
        dict(target="r", value="1.0; b:=TRUE;"),
    ],
)
def test_invalid_or_forged_inputs_are_rejected(assignment):
    with pytest.raises(DomainError):
        render(
            [
                scenario(
                    scans=[
                        dict(
                            assignments=[assignment],
                            assertions=[dict(target="b", expected="FALSE", tolerance=None)],
                        )
                    ]
                )
            ]
        )


def test_timer_true_cannot_be_simulated_by_repeated_scans():
    with pytest.raises(DomainError, match="真实定时器"):
        render(
            [
                scenario(
                    scans=[
                        dict(
                            assignments=[dict(target="b", value="TRUE")],
                            assertions=[dict(target="t.Q", expected="TRUE", tolerance=None)],
                        )
                    ]
                )
            ]
        )


def test_real_timer_regression_requires_manual_elapsed_time_and_has_no_fake_flag():
    bug = STBug(
        id="B001",
        title="定时器未复位",
        severity="high",
        confidence="confirmed",
        line_start=9,
        line_end=9,
        evidence="t(IN := b, PT := T#1S);",
        explanation="跨次保留状态",
        suggestion="复位",
        disposition="fixed",
        fix_explanation="复位",
    )
    with pytest.raises(DomainError, match="B001"):
        render([scenario(related_bug_ids=["B001"])], [bug])
    manual = scenario(
        "T002",
        execution="manual",
        related_bug_ids=["B001"],
        scans=[],
        manual_preconditions="目标PLC重新初始化",
        manual_steps=[
            "输入保持TRUE并真实等待1秒，确认Q为TRUE",
            "复位后再次输入TRUE，1秒前Q应为FALSE",
        ],
        manual_expected="两个周期均需达到1秒后Q才为TRUE",
        adaptation_note="依赖目标PLC时钟；未执行",
    )
    tests, code = render([scenario(), manual], [bug])
    assert tests[1].execution == "manual"
    assert "T002 : BOOL" not in code
    assert "AllPassed := T001;" in code


def test_large_scan_loop_is_not_representable_in_schema():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        render([scenario(scans=scenario()["scans"] * 9)])
