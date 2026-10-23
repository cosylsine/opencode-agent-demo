"""Small lexical checks and deterministic exports; never a PLC compiler or runtime."""

import difflib
import json
import re

from packages.contracts.st_code import (
    STChecks,
    STResult,
    STStart,
)
from packages.domain.core import DomainError

_WORD = r"[^\W\d]\w*"
_NUMBER = r"\d[\d_]*(?:\.\d[\d_]*)?(?:[eE][+-]?\d[\d_]*)?"
_BASE_NUMBER = r"(?:16#[0-9a-f_]+|8#[0-7_]+|2#[01_]+)"
_DATE = r"\d{4}-\d{2}-\d{2}"
_CLOCK = r"\d{2}:\d{2}:\d{2}(?:\.\d+)?"
_LEXEME = re.compile(
    rf"(?:L?DATE_AND_TIME|L?DT)#{_DATE}-{_CLOCK}"
    rf"|(?:L?TIME_OF_DAY|L?TOD)#{_CLOCK}"
    rf"|(?:L?DATE|L?D)#{_DATE}"
    rf"|(?:L?TIME|L?T)#[+-]?(?:{_NUMBER}(?:ms|us|ns|d|h|m|s))+"
    rf"|{_WORD}#(?:{_BASE_NUMBER}|[+-]?{_NUMBER}|{_WORD})"
    rf"|{_BASE_NUMBER}|{_WORD}|{_NUMBER}"
    r"|:=|=>|<>|<=|>=|\*\*|\.\.|::|\+=|-=|\*=|/=|\S",
    re.IGNORECASE,
)


def render_test_program(request, tests, declarations, checks, *, subject=None):
    ids = [test["id"] for test in tests if test.get("execution", "st") == "st"]
    if set(ids) != {check.test_id for check in checks}:
        raise DomainError("ST_OUTPUT_INVALID", "测试计划必须为每个测试编号提供实际检查", 503)
    reserved = {value.upper() for value in [*ids, "AllPassed"]}
    if len({d.name.upper() for d in declarations}) != len(declarations) or any(
        d.name.upper() in reserved for d in declarations
    ):
        raise DomainError("ST_OUTPUT_INVALID", "测试局部变量重复或占用了平台断言变量", 503)
    program_names = {name.upper() for name in re.findall(r"(?im)^\s*PROGRAM\s+(\w+)", request.code)}
    if any(d.type.upper() in program_names for d in declarations):
        raise DomainError(
            "ST_OUTPUT_INVALID",
            "PROGRAM 不能作为功能块类型实例化，请使用平台测试封装 ST_TestSubject",
            503,
        )
    instances = {
        d.name.upper()
        for d in declarations
        if subject
        and d.type.upper() == "ST_TESTSUBJECT"
        or re.search(r"(?im)^\s*FUNCTION_BLOCK\s+" + re.escape(d.type) + r"\b", request.code)
    }
    functions = {name.upper() for name in re.findall(r"(?im)^\s*FUNCTION\s+(\w+)", request.code)}
    source_has_fb = bool(re.search(r"(?im)^\s*FUNCTION_BLOCK\s+", request.code))
    if ids and (subject or source_has_fb) and not instances:
        raise DomainError("ST_OUTPUT_INVALID", "测试未声明被测功能块实例", 503)
    lines = [
        "(* 平台生成的 ST 测试草稿；未编译、未执行。AllPassed 仅汇总 ST 检查，不包含人工/时钟用例。 *)",
        *(
            ["(* 仅测试使用：将原 PROGRAM 正文封装为独立 FB，生产代码不变。 *)", subject]
            if subject
            else []
        ),
        "PROGRAM ST_GeneratedTests",
        "VAR",
        *[f"    {d.name} : {d.type};" for d in declarations],
        *[f"    {name} : BOOL;" for name in ids],
        "    AllPassed : BOOL;",
        "END_VAR",
    ]
    for test_id in ids:
        lines.extend([f"(* {test_id} *)", f"{test_id} := TRUE;"])
        for check in (item for item in checks if item.test_id == test_id):
            actual_tokens, expected_tokens = tokens(check.actual), tokens(check.expected)
            if (
                not actual_tokens
                or any(token in {";", ":="} for token in expected_tokens)
                or any(token == ";" for token in actual_tokens)
            ):
                raise DomainError(
                    "ST_OUTPUT_INVALID", "actual/expected 必须是表达式，不能包含独立语句", 503
                )
            scalar = check.expected.strip()
            if not re.fullmatch(
                r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|TRUE|FALSE|'(?:[^']|'')*'",
                scalar,
                re.IGNORECASE,
            ):
                raise DomainError(
                    "ST_OUTPUT_INVALID", "测试 expected 必须是直接的数值、BOOL 或字符串常量", 503
                )
            if check.setup:
                setup_tokens = tokens(check.setup)
                if any(
                    word.upper()
                    in {
                        "VAR",
                        "VAR_INPUT",
                        "VAR_OUTPUT",
                        "VAR_IN_OUT",
                        "VAR_TEMP",
                        "END_VAR",
                        "PROGRAM",
                        "FUNCTION",
                        "FUNCTION_BLOCK",
                        "END_PROGRAM",
                        "END_FUNCTION",
                        "END_FUNCTION_BLOCK",
                    }
                    for word in setup_tokens
                ):
                    raise DomainError(
                        "ST_OUTPUT_INVALID", "setup 只能包含前置语句，不能定义程序或变量", 503
                    )
                if any(
                    value.upper() in reserved and setup_tokens[i + 1] == ":="
                    for i, value in enumerate(setup_tokens[:-1])
                ):
                    raise DomainError("ST_OUTPUT_INVALID", "setup 不得改写平台断言变量", 503)
                lines.append(check.setup)
            invoked = {name.upper() for name in re.findall(r"\b(\w+)\s*\(", check.setup)}
            if functions and not functions.intersection(
                invoked | {name.upper() for name in re.findall(r"\b(\w+)\s*\(", check.actual)}
            ):
                raise DomainError("ST_OUTPUT_INVALID", f"{test_id} 没有调用被测函数", 503)
            if instances and not instances.intersection(invoked):
                raise DomainError(
                    "ST_OUTPUT_INVALID", f"{test_id} 检查前没有调用被测实例执行扫描", 503
                )
            if instances and not any(
                re.search(r"\b" + re.escape(name) + r"\s*\.", check.actual, re.IGNORECASE)
                for name in instances
            ):
                raise DomainError("ST_OUTPUT_INVALID", f"{test_id} 没有读取被测实例结果", 503)
            if check.tolerance is None:
                expression = f"({check.actual}) = ({scalar})"
            else:
                if not re.fullmatch(r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", scalar):
                    raise DomainError("ST_OUTPUT_INVALID", "容差比较仅支持数值预期", 503)
                expression = f"ABS(({check.actual}) - ({scalar})) <= {check.tolerance}"
            lines.append(f"{test_id} := {test_id} AND ({expression});")
    lines.extend(["AllPassed := " + (" AND ".join(ids) if ids else "FALSE") + ";", "END_PROGRAM"])
    for case in tests:
        if case.get("execution") == "manual":
            note = (
                (case["id"] + " 人工/PLC时钟用例：" + case.get("adaptation_note", ""))
                .replace("*)", "* )")
                .replace("(*", "( *")
            )
            lines.append("(* " + note + " *)")
    return "\n".join(lines) + "\n"


def tokens(code):
    """Retain exact tokens, strings and pragmas; discard whitespace and ST comments.

    Both IEC doubled quotes and Siemens $ escapes are kept verbatim. Nested block
    comments are accepted. Unknown syntax is retained rather than reinterpreted.
    """
    result, pos = [], 0
    while pos < len(code):
        char = code[pos]
        string_prefix = re.match(r"(?:W?STRING|W?CHAR)#(?=['\"])", code[pos:], re.IGNORECASE)
        if char.isspace():
            pos += 1
        elif code.startswith("//", pos):
            end = code.find("\n", pos)
            pos = len(code) if end < 0 else end
        elif code.startswith("(*", pos):
            pos, depth = pos + 2, 1
            while pos < len(code) and depth:
                if code.startswith("(*", pos):
                    depth, pos = depth + 1, pos + 2
                elif code.startswith("*)", pos):
                    depth, pos = depth - 1, pos + 2
                else:
                    pos += 1
            if depth:
                raise ValueError("块注释未闭合")
        elif char in "'\"" or string_prefix:
            start = pos
            if string_prefix:
                pos += len(string_prefix[0])
                char = code[pos]
            pos += 1
            while pos < len(code):
                if code[pos] == "$" and pos + 1 < len(code):
                    pos += 2
                elif code[pos] == char:
                    pos += 1
                    if pos < len(code) and code[pos] == char:
                        pos += 1
                    else:
                        break
                else:
                    pos += 1
            else:
                raise ValueError("字符串未闭合")
            result.append(code[start:pos])
        elif char == "{":
            # Pragmas can affect compiled behavior. Never strip or modify them
            # as if they were explanatory comments.
            end = code.find("}", pos + 1)
            if end < 0:
                raise ValueError("编译指令未闭合")
            result.append(code[pos : end + 1])
            pos = end + 1
        else:
            match = _LEXEME.match(code, pos)
            result.append(match[0])
            pos += len(match[0])
    return result


def basic_st_issues(code, source="", *, test=False):
    """Reject common generation defects; this is deliberately not a compiler.

    END_IF syntax: https://content.helpme-codesys.com/en/CODESYS%20Development%20System/_cds_st_instruction_if.html
    Declarations: https://infosys.beckhoff.com/content/1033/tc3_plc_intro/2526557579.html
    """
    try:
        seq = tokens(code)
    except ValueError as exc:
        return [str(exc)]
    upper = [word.upper() for word in seq]
    issues, blocks, brackets = [], [], []
    closes = {
        "END_IF": "IF",
        "END_CASE": "CASE",
        "END_FOR": "FOR",
        "END_WHILE": "WHILE",
        "END_REPEAT": "REPEAT",
    }
    for i, word in enumerate(upper):
        if word in closes.values():
            blocks.append(word)
        elif word in closes:
            if not blocks or blocks.pop() != closes[word]:
                issues.append(f"{word} 与开始语句不匹配")
            if i + 1 >= len(seq) or seq[i + 1] != ";":
                issues.append(f"{word} 后缺少分号 ;")
        if word in ("(", "["):
            brackets.append(word)
        elif word in (")", "]"):
            if not brackets or brackets.pop() != {")": "(", "]": "["}[word]:
                issues.append("括号不匹配")
    if blocks or brackets:
        issues.append("控制块或括号未闭合")
    if not test:
        return issues
    pou_names = {
        name.upper()
        for name in re.findall(
            r"(?im)^\s*(?:FUNCTION_BLOCK|FUNCTION|PROGRAM)\s+(\w+)", code + "\n" + source
        )
    }
    if not any(word in upper for word in ("PROGRAM", "FUNCTION_BLOCK", "FUNCTION")):
        issues.append("测试代码必须包含完整 PROGRAM、FUNCTION_BLOCK 或 FUNCTION 声明")
    periods = 0
    for word in seq:
        periods = periods + len(word) if set(word) == {"."} else 0
        if periods >= 3:
            issues.append("测试代码包含省略号占位符，必须给出实际 ST 语句")
            break
    declared, declared_types = set(pou_names), {}
    in_declaration, declaration, declaration_positions = False, [], set()
    for i, word in enumerate(upper):
        if word in {
            "VAR",
            "VAR_INPUT",
            "VAR_OUTPUT",
            "VAR_IN_OUT",
            "VAR_TEMP",
            "VAR_GLOBAL",
            "VAR_EXTERNAL",
        }:
            in_declaration, declaration = True, []
        elif word == "END_VAR":
            if declaration:
                issues.append("测试变量声明缺少分号")
            in_declaration = False
        elif in_declaration:
            declaration_positions.add(i)
            if word == ";":
                if declaration and ":" not in declaration:
                    issues.append("测试变量声明缺少冒号，例如 r : REAL;")
                elif declaration:
                    for name in declaration[: declaration.index(":")]:
                        if re.fullmatch(_WORD, name) and name not in {
                            "CONSTANT",
                            "RETAIN",
                            "NON_RETAIN",
                            "PERSISTENT",
                        }:
                            declared.add(name)
                            colon = declaration.index(":")
                            if colon + 1 < len(declaration):
                                declared_types[name] = declaration[colon + 1]
                declaration = []
            else:
                declaration.append(word)
    if in_declaration:
        issues.append("测试变量声明缺少 END_VAR")
    builtins = {
        "ABS",
        "MIN",
        "MAX",
        "LIMIT",
        "SEL",
        "MUX",
        "SQRT",
        "EXP",
        "EXPT",
        "LN",
        "LOG",
        "SIN",
        "COS",
        "TAN",
        "ASIN",
        "ACOS",
        "ATAN",
        "TRUNC",
        "ROUND",
        "SHL",
        "SHR",
        "ROL",
        "ROR",
    }
    depth = 0
    for i, word in enumerate(upper[:-1]):
        if word == "(":
            depth += 1
        elif word == ")":
            depth -= 1
        if i in declaration_positions or not re.fullmatch(_WORD, word):
            continue
        member = i > 0 and seq[i - 1] == "."
        if seq[i + 1] == ":=" and depth == 0 and word not in declared and not member:
            issues.append(f"测试变量 {seq[i]} 未声明")
        if (
            seq[i + 1] == ":="
            and depth == 0
            and declared_types.get(word)
            in {"REAL", "LREAL", "SINT", "INT", "DINT", "LINT", "USINT", "UINT", "UDINT", "ULINT"}
        ):
            nested_depth = 0
            for value in upper[i + 2 :]:
                if value == ";":
                    break
                if value == "(":
                    nested_depth += 1
                elif value == ")":
                    nested_depth -= 1
                elif nested_depth == 0 and value in {
                    "AND",
                    "OR",
                    "XOR",
                    "NOT",
                    "=",
                    "<>",
                    "<",
                    ">",
                    "<=",
                    ">=",
                }:
                    issues.append(
                        f"测试变量 {seq[i]} 是数值类型，却被赋予布尔表达式；断言变量必须声明为 BOOL"
                    )
                    break
        if (
            seq[i + 1] == "("
            and word not in declared | builtins
            and "_TO_" not in word
            and word not in {"IF", "ELSIF", "WHILE", "UNTIL", "NOT", "AND", "OR", "XOR", "RETURN"}
        ):
            issues.append(f"测试辅助函数 {seq[i]} 未定义；使用 ABS 等内置表达式或提供完整实现")
    # Functions require their declared inputs; FB instance calls may intentionally
    # omit retained inputs. This check therefore applies to FUNCTION only.
    for name, block in re.findall(
        r"(?is)\bFUNCTION\s+(\w+)\s*:[^;]*?\bVAR_INPUT(.*?)END_VAR", source
    ):
        required = []
        for declaration in block.split(";"):
            if ":" in declaration and ":=" not in declaration:
                required.extend(
                    value.strip().upper() for value in declaration.split(":", 1)[0].split(",")
                )
        for i in range(len(upper) - 1):
            if upper[i] != name.upper() or seq[i + 1] != "(":
                continue
            level, parts, part = 1, [], []
            for value in seq[i + 2 :]:
                if value == "(":
                    level += 1
                elif value == ")":
                    level -= 1
                    if level == 0:
                        if part:
                            parts.append(part)
                        break
                if value == "," and level == 1:
                    parts.append(part)
                    part = []
                else:
                    part.append(value)
            named = {part[0].upper() for part in parts if len(part) > 1 and part[1] == ":="}
            if (named and not set(required).issubset(named)) or (
                not named and len(parts) < len(required)
            ):
                issues.append(f"测试调用 {name} 缺少输入参数，必须传入 {', '.join(required)}")
    return list(dict.fromkeys(issues))


def validate_result(request: STStart, result: STResult):
    def invalid(message):
        raise DomainError("ST_OUTPUT_INVALID", message, 503)

    selected = set(request.actions)
    checks = STChecks(
        annotation_tokens_preserved=None,
        fixed_lexically_valid=None,
        scan_verification=result.scan_verification,
    )
    if result.scan_verification_note:
        checks.note += " " + result.scan_verification_note
    for action, value in (
        ("comment", result.annotated_code),
        ("test", result.test_code),
        ("fix", result.fixed_code),
    ):
        if (action in selected) != (value is not None):
            invalid(f"{action} 产物与所选操作不一致")
        if value is not None and (not value.strip() or value.lstrip().startswith("```")):
            invalid(f"{action} 必须返回无 Markdown 围栏的代码")
    if "comment" in selected:
        try:
            if tokens(request.code) != tokens(result.annotated_code):
                invalid("注释版本改变了原代码的有效词元，请只添加注释")
        except ValueError as exc:
            invalid(f"注释版本无法通过词法检查：{exc}")
        checks.annotation_tokens_preserved = True
    if "test" in selected and not result.tests:
        invalid("生成测试时必须包含测试步骤和预期结果")
    if "test" not in selected and result.tests:
        invalid("未选择测试操作")
    syntax_issues = []
    if result.test_code is not None:
        syntax_issues.extend(
            "测试草稿：" + message
            for message in basic_st_issues(result.test_code, request.code, test=True)
        )
    if result.fixed_code is not None:
        syntax_issues.extend(
            "修复代码：" + message for message in basic_st_issues(result.fixed_code)
        )
    if syntax_issues:
        invalid("；".join(syntax_issues)[:1500])
    if not selected.intersection({"review", "fix"}) and result.bugs:
        invalid("未选择问题检查或修复")
    lines = request.code.splitlines()
    ids = [bug.id for bug in result.bugs]
    if len(ids) != len(set(ids)) or len({t.id for t in result.tests}) != len(result.tests):
        invalid("问题或测试编号重复")
    for bug in result.bugs:
        if not 1 <= bug.line_start <= bug.line_end <= len(lines):
            invalid(f"{bug.id} 的原始代码行号无效")
        excerpt = "\n".join(lines[bug.line_start - 1 : bug.line_end])
        if bug.evidence.strip() not in excerpt:
            invalid(f"{bug.id} 的证据与原始代码不一致")
        if bug.disposition == "fixed" and ("fix" not in selected or bug.confidence != "confirmed"):
            invalid(f"{bug.id} 仅允许在修复操作中修复已确认问题")
    for test in result.tests:
        if any(value not in ids for value in test.related_bug_ids):
            invalid(f"{test.id} 引用了不存在的问题")
    if "fix" in selected:
        try:
            fixed_tokens = tokens(result.fixed_code)
            if not fixed_tokens:
                invalid("修复版本没有有效代码")
        except ValueError as exc:
            invalid(f"修复版本无法通过词法检查：{exc}")
        try:
            changed = tokens(request.code) != fixed_tokens
        except ValueError:
            # A malformed source string/comment may itself be the confirmed bug.
            # Only the repaired output must pass lexical checks in fix-only runs.
            changed = True
        fixed = [bug for bug in result.bugs if bug.disposition == "fixed"]
        if changed != bool(fixed):
            invalid("修复代码的有效词元变化与问题修复记录不一致")
        uncovered = [
            bug.id
            for bug in fixed
            if not any(bug.id in test.related_bug_ids for test in result.tests)
        ]
        if "test" in selected and uncovered:
            invalid(
                "以下修复问题缺少回归测试关联："
                + "、".join(uncovered)
                + "。语法修复可列目标 IDE 人工编译用例，不能省略。"
            )
        checks.fixed_lexically_valid = True
    return checks


def exports(run):
    """Build filenames/content only from validated, persisted results."""
    source = run.request.code
    output = {"original.st": source}
    result = run.result
    if result is None:
        return output
    if result.annotated_code is not None:
        output["annotated.st"] = result.annotated_code
    if result.fixed_code is not None:
        output["fixed.st"] = result.fixed_code
        output["changes.diff"] = "".join(
            difflib.unified_diff(
                source.splitlines(keepends=True),
                result.fixed_code.splitlines(keepends=True),
                fromfile="original.st",
                tofile="fixed.st",
            )
        )
    if result.test_code is not None:
        output["tests.st"] = result.test_code
        output["test-cases.json"] = json.dumps(
            [t.model_dump() for t in result.tests], ensure_ascii=False, indent=2
        )
    report = [
        "# ST 代码优化报告",
        result.summary,
        f"工作流：{run.workflow_version}；模型：{run.model_id}",
        f"原始文件：{run.request.filename}；SHA-256：{run.source_sha256}",
        f"ST 方言：{run.request.dialect}；操作：{', '.join(run.request.actions)}",
        "工程上下文与要求：" + (run.request.instructions or "未提供"),
        "验证状态：未调用 PLC 编译器，测试用例未在目标 PLC 执行。修复为待工程验证的代码建议。",
        "逻辑扫描预期校验：" + (result.scan_verification_note or "本次未运行本地扫描模型。"),
        "## 假设与待确认项",
        *[f"- {a}" for a in result.assumptions],
        "## 问题",
    ]
    for b in result.bugs:
        report.extend(
            [
                f"### {b.id} {b.title}",
                f"原文第 {b.line_start}–{b.line_end} 行；严重程度 {b.severity}；可信度 {b.confidence}；处置 {b.disposition}",
                "```st\n" + b.evidence + "\n```",
                b.explanation,
                b.suggestion,
                b.fix_explanation,
            ]
        )
    if not result.bugs:
        report.append(
            "本轮未报告问题，不代表代码不存在缺陷。"
            if set(run.request.actions).intersection({"review", "fix"})
            else "本次未选择问题检查或修复操作，未进行缺陷审查。"
        )
    report.append("## 测试用例（未执行）")
    for t in result.tests:
        report.extend(
            [
                f"### {t.id} {t.title} ({t.category})",
                t.preconditions,
                "执行方式："
                + (
                    "人工/目标 PLC 时钟验证；" + t.adaptation_note
                    if t.execution == "manual"
                    else "ST 检查草稿（未执行）"
                ),
                *[f"{i}. {step}" for i, step in enumerate(t.steps, 1)],
                "预期：" + t.expected,
                "关联问题：" + ", ".join(t.related_bug_ids),
            ]
        )
    output["report.md"] = "\n\n".join(report) + "\n"
    output["result.json"] = json.dumps(
        dict(
            result=result.model_dump(),
            checks=run.checks.model_dump(),
            source_sha256=run.source_sha256,
            workflow_version=run.workflow_version,
            model_id=run.model_id,
            attempt_count=run.attempt_count,
            request=run.request.model_dump(exclude={"code"}),
            validation_feedback=run.validation_feedback,
        ),
        ensure_ascii=False,
        indent=2,
    )
    return output
