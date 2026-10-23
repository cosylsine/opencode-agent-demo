"""Generate bounded, isolated scan tests and their documentation from one plan."""

import re

from packages.adapters.st_code import tokens
from packages.contracts.st_code import STTestCase
from packages.domain.core import DomainError


def invalid(message):
    raise DomainError("ST_OUTPUT_INVALID", message, 503)


def variables(source):
    seq = tokens(source)
    result, block, declaration = {}, False, []
    for word in seq:
        upper = word.upper()
        if upper in {
            "VAR",
            "VAR_INPUT",
            "VAR_OUTPUT",
            "VAR_IN_OUT",
            "VAR_TEMP",
            "VAR_EXTERNAL",
            "VAR_GLOBAL",
        }:
            block, declaration = True, []
        elif upper == "END_VAR":
            block, declaration = False, []
        elif block:
            if word != ";":
                declaration.append(word)
                continue
            if ":" in declaration:
                colon = declaration.index(":")
                type_tokens = declaration[colon + 1 :]
                kind = type_tokens[0].upper() if type_tokens else ""
                bounds = None
                if kind == "ARRAY" and "OF" in [v.upper() for v in type_tokens]:
                    of = [v.upper() for v in type_tokens].index("OF")
                    kind = type_tokens[of + 1].upper()
                    array = "".join(type_tokens[:of])
                    match = re.fullmatch(r"ARRAY\[([0-9]+)\.\.([0-9]+)\]", array, re.IGNORECASE)
                    if match:
                        bounds = (int(match[1]), int(match[2]))
                for name in declaration[:colon]:
                    if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", name) and name.upper() not in {
                        "RETAIN",
                        "NON_RETAIN",
                        "CONSTANT",
                        "PERSISTENT",
                    }:
                        result[name.upper()] = dict(name=name, kind=kind, bounds=bounds)
            declaration = []
    return result


def scalar(value):
    seq = tokens(value)
    if re.fullmatch(r"[+-]?[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?", value):
        return "number"
    if value.upper() in {"TRUE", "FALSE"}:
        return "bool"
    if len(seq) == 1 and value.startswith("'") and value.endswith("'"):
        return "string"
    if len(seq) == 1 and re.fullmatch(
        r"(?:T|TIME|LT|LTIME)#[+-]?(?:[0-9]+(?:\.[0-9]+)?(?:ms|us|ns|d|h|m|s))+",
        value,
        re.IGNORECASE,
    ):
        return "time"
    invalid("扫描输入和预期只能是直接数值、BOOL、字符串或时间常量，不能包含表达式或代码")


def path_info(target, declared, *, writing=False):
    match = re.fullmatch(
        r"([A-Za-z_][A-Za-z_0-9]*)(?:\[([0-9]+)\])?(?:\.([A-Za-z_][A-Za-z_0-9]*))?", target
    )
    if not match or match[1].upper() not in declared:
        invalid(f"扫描计划引用了未声明的被测变量：{target}")
    info = declared[match[1].upper()]
    if match[2] is not None:
        if not info["bounds"] or not info["bounds"][0] <= int(match[2]) <= info["bounds"][1]:
            invalid(f"扫描数组下标超出声明范围：{target}")
    elif info["bounds"]:
        invalid(f"数组必须逐元素提供固定下标：{target}")
    if match[3]:
        if info["kind"] not in {"TON", "TOF", "TP", "R_TRIG", "F_TRIG"} or match[3].upper() not in {
            "Q",
            "ET",
        }:
            invalid(f"尚未展开该成员类型，请改为目标工程人工用例：{target}")
        if writing:
            invalid(
                "不能直接改写功能块内部状态或定时器 Q/ET 来伪造测试前提，请使用真实时钟人工用例"
            )
    elif info["kind"] in {"TON", "TOF", "TP", "R_TRIG", "F_TRIG"}:
        invalid("不能整体赋值或比较功能块实例")
    return info, match[3].upper() if match[3] else None


def render_scenarios(request, result, plan, subject):
    source = result.fixed_code or request.code
    declared = variables(source)
    fb = re.search(r"(?im)^\s*FUNCTION_BLOCK\s+(\w+)", source)
    type_name = "ST_TestSubject" if subject else fb[1] if fb else None
    if not type_name:
        invalid("扫描测试需要一个 PROGRAM 或 FUNCTION_BLOCK；其他 POU 请使用相应函数测试")
    ids = [case.id for case in plan.scenarios]
    if len(set(ids)) != len(ids):
        invalid("测试编号重复")
    automatic = [case for case in plan.scenarios if case.execution == "st"]
    body, tests, manuals = [], [], []
    for case in plan.scenarios:
        if case.execution == "manual":
            if (
                case.scans
                or not case.manual_preconditions
                or not case.manual_steps
                or not case.manual_expected
                or not case.adaptation_note
            ):
                invalid(f"{case.id} 人工用例必须给出前提、操作、预期和适配条件，不能包含自动扫描")
            note = case.adaptation_note
            if subject and not any(word.upper() == "END_PROGRAM" for word in tokens(request.code)):
                note += "；原输入为程序片段，独立文件须在目标 IDE 补齐 POU 边界。"
            tests.append(
                STTestCase(
                    id=case.id,
                    title=case.title,
                    category=case.category,
                    related_bug_ids=case.related_bug_ids,
                    execution="manual",
                    preconditions=case.manual_preconditions,
                    steps=case.manual_steps,
                    expected=case.manual_expected,
                    adaptation_note=note,
                )
            )
            manuals.append(case)
            continue
        if not case.scans:
            invalid(f"{case.id} 自动用例必须给出至少一个扫描步骤")
        instance = "Sut_" + case.id
        steps, expected = [], []
        body.extend([f"(* {case.id} 独立实例；每个步骤只调用一次。 *)", f"{case.id} := TRUE;"])
        for index, scan in enumerate(case.scans, 1):
            assigned = set()
            inputs = []
            for assignment in scan.assignments:
                if assignment.target.upper() in assigned:
                    invalid(f"{case.id} 同一扫描重复设置 {assignment.target}")
                assigned.add(assignment.target.upper())
                info, _ = path_info(assignment.target, declared, writing=True)
                value_type = scalar(assignment.value)
                if info["kind"] == "BOOL" and value_type != "bool":
                    invalid("BOOL 输入必须为 TRUE 或 FALSE")
                if (
                    info["kind"]
                    in {
                        "REAL",
                        "LREAL",
                        "INT",
                        "DINT",
                        "SINT",
                        "LINT",
                        "UINT",
                        "UDINT",
                        "USINT",
                        "ULINT",
                    }
                    and value_type != "number"
                ):
                    invalid("数值变量必须使用数值常量")
                if info["kind"] in {"TON", "TOF", "TP", "R_TRIG", "F_TRIG"}:
                    invalid("不能整体覆盖功能块实例")
                body.append(f"{instance}.{assignment.target} := {assignment.value};")
                inputs.append(f"{assignment.target} := {assignment.value}")
            body.append(instance + "();")
            steps.append(
                f"扫描 {index} 前："
                + ("；".join(inputs) if inputs else "保持本用例上一扫描输入")
                + "；调用一次被测实例。"
            )
            expectations = []
            for assertion in scan.assertions:
                info, member = path_info(assertion.target, declared)
                value_type = scalar(assertion.expected)
                if (member == "Q" or info["kind"] == "BOOL") and value_type != "bool":
                    invalid("BOOL 预期必须为 TRUE 或 FALSE")
                if (
                    info["kind"]
                    in {
                        "REAL",
                        "LREAL",
                        "INT",
                        "DINT",
                        "SINT",
                        "LINT",
                        "UINT",
                        "UDINT",
                        "USINT",
                        "ULINT",
                    }
                    and value_type != "number"
                ):
                    invalid("数值预期必须是数值常量")
                if member in {"Q", "ET"} and info["kind"] in {"TON", "TOF", "TP"}:
                    if (member == "Q" and assertion.expected.upper() == "TRUE") or (
                        member == "ET"
                        and not re.fullmatch(
                            r"(?:T|TIME|LT|LTIME)#0(?:ms|us|ns|d|h|m|s)",
                            assertion.expected,
                            re.IGNORECASE,
                        )
                    ):
                        invalid(
                            "真实定时器非零耗时或 Q=TRUE 需要人工时钟用例，不能靠同一任务内重复调用模拟时间"
                        )
                actual = instance + "." + assertion.target
                if assertion.tolerance is not None:
                    if value_type != "number":
                        invalid("仅数值检查可设置容差")
                    expression = (
                        f"ABS(({actual}) - ({assertion.expected})) <= {assertion.tolerance}"
                    )
                    description = (
                        f"{assertion.target} = {assertion.expected}（容差 {assertion.tolerance}）"
                    )
                else:
                    if info["kind"] in {"REAL", "LREAL"}:
                        invalid("REAL/LREAL 检查必须给出数值容差")
                    expression = f"({actual}) = ({assertion.expected})"
                    description = assertion.target + " = " + assertion.expected
                body.append(f"{case.id} := {case.id} AND ({expression});")
                expectations.append(description)
            steps.append(f"扫描 {index} 后检查：" + "；".join(expectations) + "。")
            expected.append(f"扫描 {index}：" + "；".join(expectations))
        tests.append(
            STTestCase(
                id=case.id,
                title=case.title,
                category=case.category,
                related_bug_ids=case.related_bug_ids,
                execution="st",
                preconditions="重新初始化测试工程后运行一次；每个用例使用独立实例，采用源程序声明的默认初值。",
                steps=steps,
                expected="。".join(expected),
                adaptation_note=case.adaptation_note,
            )
        )
    # A reset while Q was never true cannot regress a cross-batch timer defect.
    timer_names = [
        info["name"] for info in declared.values() if info["kind"] in {"TON", "TOF", "TP"}
    ]
    for bug in result.bugs:
        if bug.disposition == "fixed" and (
            "分号" in bug.title
            or any(
                re.search(r"\b" + re.escape(name) + r"\s*\(", bug.evidence, re.IGNORECASE)
                for name in timer_names
            )
        ):
            if not any(bug.id in case.related_bug_ids for case in manuals):
                invalid(
                    f"{bug.id} 必须提供目标 IDE 编译或真实时钟人工回归用例，普通单次扫描不足以验证"
                )
    names = [case.id for case in automatic]
    lines = [
        "(* 平台从扫描数据生成。未编译、未执行；各用例独立实例，仅首扫描执行。重新测试须重新初始化工程。 *)",
        *([subject] if subject else []),
        "PROGRAM ST_GeneratedTests",
        "VAR",
        *[f"    Sut_{case.id} : {type_name};" for case in automatic],
        *[f"    {name} : BOOL;" for name in names],
        "    AllPassed : BOOL;",
        "    Executed : BOOL := FALSE;",
        "END_VAR",
        "IF NOT Executed THEN",
        *body,
        "AllPassed := " + (" AND ".join(names) if names else "FALSE") + ";",
        "Executed := TRUE;",
        "END_IF;",
        "END_PROGRAM",
    ]
    for case in manuals:
        note = (
            (case.id + " 人工用例：" + case.adaptation_note)
            .replace("*)", "* )")
            .replace("(*", "( *")
        )
        lines.append("(* " + note + " *)")
    return tests, "\n".join(lines) + "\n"
