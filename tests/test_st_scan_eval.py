from pathlib import Path

import pytest

from packages.adapters.st_scan_eval import Machine, verify_scans
from packages.adapters.st_workflow import repair_baseline
from packages.contracts.st_code import STScenario
from packages.domain.core import DomainError

SOURCE = (Path(__file__).parent / "fixtures/st/batch_mixer.st").read_text()


def case(expected):
    return STScenario(
        id="T030",
        title="批次边界",
        category="boundary",
        related_bug_ids=[],
        execution="st",
        scans=[
            dict(
                assignments=[
                    dict(target="eStep", value="1"),
                    dict(target="rTotal", value="49.9"),
                    dict(target="rFlow", value="600.0"),
                ],
                assertions=[
                    dict(target="rTotal", expected=expected, tolerance=0.001),
                    dict(target="eStep", expected="2", tolerance=None),
                ],
            )
        ],
        manual_preconditions="",
        manual_steps=[],
        manual_expected="",
        adaptation_note="",
    )


def test_catches_exact_real_model_arithmetic_error_in_batch_total():
    with pytest.raises(DomainError, match="T030.*59.9"):
        verify_scans(repair_baseline(SOURCE), [case("50.9")])
    assert "核对 1 个" in verify_scans(repair_baseline(SOURCE), [case("59.9")])


def test_reports_all_mismatches_without_rewriting_any_expectations():
    first, second = case("50.0"), case("50.9")
    second.id = "T031"
    first.scans[0].assertions[1].expected = "1"
    with pytest.raises(DomainError) as error:
        verify_scans(repair_baseline(SOURCE), [first, second])
    assert all(text in error.value.message for text in ["T030", "T031", "rTotal", "eStep"])
    assert first.scans[0].assertions[0].expected == "50.0"
    assert first.scans[0].assertions[1].expected == "1"


def test_unsupported_later_assertion_does_not_hide_known_mismatch():
    value = case("50.0")
    value.scans[0].assertions[1].target = "UnknownVariable"
    with pytest.raises(DomainError, match="59.9"):
        verify_scans(repair_baseline(SOURCE), [value])


@pytest.mark.asyncio
@pytest.mark.parametrize("corrected", [True, False])
async def test_real_failure_retry_has_original_scenario_and_requires_correct_math(corrected):
    from packages.adapters.st_workflow import stage_call
    from packages.contracts.st_code import STScenariosStage, STStart

    sent = []
    request = STStart(schema_version="1.0", code=SOURCE)

    class Client:
        async def st_generate(self, schema, payload, instruction, request_id):
            sent.append(payload)
            if len(sent) == 1:
                return schema(**payload["source_receipt"], scenarios=[case("50.0")])
            # The previous plan must be supplied with its original ID, inputs,
            # assertions and tolerances, not just a reference to a missing T030.
            previous = schema.model_validate(payload["previous_output"])
            assert previous.scenarios == [case("50.0")]
            assert "T030" in payload["correction"] and "59.9" in payload["correction"]
            if corrected:
                previous.scenarios[0].scans[0].assertions[0].expected = "59.9"
            return previous

    async def run():
        return await stage_call(
            Client(), request, "tests", STScenariosStage, {}, "",
            lambda session, **progress: None,
            lambda value: verify_scans(repair_baseline(SOURCE), value.scenarios),
        )

    if corrected:
        assert "核对 1 个" in await run()
    else:
        with pytest.raises(DomainError, match="两次结果未通过"):
            await run()
    assert len(sent) == 2


def test_original_edge_and_mean_defects_differ_from_fixed_logic():
    machine = Machine(SOURCE)
    machine.state["BSTART"] = True
    for i in range(1, 7):
        machine.state["ATEMP"][i] = i * 10.0
    machine.scan()
    assert machine.state["BSTARTEDGE"] is False
    assert machine.state["RTEMPAVG"] == 10.0
    fixed = repair_baseline(SOURCE).replace(
        "FOR i := 1 TO 6 DO\n    rTempAvg := 0.0;", "rTempAvg := 0.0;\nFOR i := 1 TO 6 DO"
    )
    machine = Machine(fixed)
    machine.state["BSTART"] = True
    for i in range(1, 7):
        machine.state["ATEMP"][i] = i * 10.0
    machine.scan()
    assert machine.state["BSTARTEDGE"] is True
    assert machine.state["RTEMPAVG"] == 35.0 and machine.state["ESTEP"] == 1


def test_same_case_state_retained_but_new_cases_are_independent():
    fixed = repair_baseline(SOURCE)
    first, second = Machine(fixed), Machine(fixed)
    first.state["BSTART"] = True
    first.scan()
    assert first.state["BSTARTEDGE"] is True
    first.scan()
    assert first.state["BSTARTEDGE"] is False
    assert second.state["BSTARTOLD"] is False


def test_zero_time_ton_never_invents_twenty_seconds():
    machine = Machine(repair_baseline(SOURCE))
    machine.state["ESTEP"] = 3
    for _ in range(8):
        machine.scan()
    assert machine.state["TOUT"]["Q"] is False
    assert machine.state["TOUT"]["ET"] == 0.0


def test_unsupported_syntax_and_unbounded_loops_are_explicitly_unverified():
    source = "PROGRAM P\nVAR r:REAL; END_VAR\nr:=SQRT(4.0);\nEND_PROGRAM"
    value = STScenario.model_validate(
        dict(
            id="T001",
            title="未知内置函数",
            category="normal",
            related_bug_ids=[],
            execution="st",
            scans=[
                dict(assignments=[], assertions=[dict(target="r", expected="2.0", tolerance=0.001)])
            ],
            manual_preconditions="",
            manual_steps=[],
            manual_expected="",
            adaptation_note="",
        )
    )
    assert "尚未覆盖" in verify_scans(source, [value])
    source = "PROGRAM P\nVAR r:REAL; i:INT; END_VAR\nFOR i:=1 TO 1000000 DO r:=r+1.0; END_FOR; END_PROGRAM"
    assert "尚未覆盖" in verify_scans(source, [value])


def test_integer_arithmetic_and_negative_modulo_are_not_python_floor_division():
    source = "PROGRAM P\nVAR a:INT; b:INT; END_VAR\na := -5 / 2; b := -5 MOD 2; END_PROGRAM"
    machine = Machine(source)
    machine.scan()
    assert machine.state["A"] == -2 and machine.state["B"] == -1
