"""Bounded, zero-time ST subset evaluator for checking generated scan expectations.

This is neither a PLC compiler nor a real-time runtime. No Python eval, external
calls, I/O mappings, or arbitrary user functions are supported.
"""

import math
import re
import struct

from packages.adapters.st_code import tokens
from packages.domain.core import DomainError


class UnsupportedST(ValueError):
    pass


def literal(value):
    upper = value.upper()
    if upper in {"TRUE", "FALSE"}:
        return upper == "TRUE"
    if value.startswith("'") and value.endswith("'"):
        if "$" in value:
            raise UnsupportedST("字符串转义尚未纳入扫描模型")
        return value[1:-1].replace("''", "'")
    if re.fullmatch(
        r"(?:T|TIME|LT|LTIME)#[+-]?(?:\d+(?:\.\d+)?(?:ms|us|ns|d|h|m|s))+", value, re.IGNORECASE
    ):
        duration = value.split("#", 1)[1]
        units = {
            "d": 86400,
            "h": 3600,
            "m": 60,
            "s": 1,
            "ms": 0.001,
            "us": 0.000001,
            "ns": 0.000000001,
        }
        return sum(
            float(number) * units[unit.lower()]
            for number, unit in re.findall(
                r"([+-]?\d+(?:\.\d+)?)(ms|us|ns|d|h|m|s)", duration, re.IGNORECASE
            )
        )
    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", value):
        return float(value) if re.search(r"[.eE]", value) else int(value)
    raise UnsupportedST("常量类型尚未纳入扫描模型")


class Parser:
    precedence = {
        "OR": 1,
        "XOR": 2,
        "AND": 3,
        "=": 4,
        "<>": 4,
        "<": 4,
        ">": 4,
        "<=": 4,
        ">=": 4,
        "+": 5,
        "-": 5,
        "*": 6,
        "/": 6,
        "MOD": 6,
    }

    def __init__(self, source):
        self.seq, self.pos = tokens(source), 0

    def peek(self, offset=0):
        return self.seq[self.pos + offset].upper() if self.pos + offset < len(self.seq) else ""

    def take(self, expected=None):
        if self.pos >= len(self.seq):
            raise UnsupportedST("片段结束")
        value = self.seq[self.pos]
        self.pos += 1
        if expected is not None and value.upper() != expected:
            raise UnsupportedST("语法不在受限扫描子集中：需要 " + expected)
        return value

    def optional(self, word):
        if self.peek() == word:
            self.take()
            return True
        return False

    def expr(self, minimum=0):
        if self.peek() in {"NOT", "+", "-"}:
            left = ("unary", self.take().upper(), self.expr(8))
        elif self.optional("("):
            left = self.expr()
            self.take(")")
        else:
            value = self.take()
            try:
                left = ("literal", literal(value))
            except UnsupportedST:
                if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", value):
                    raise UnsupportedST("不支持该表达式词元") from None
                if self.optional("("):
                    args = []
                    if self.peek() != ")":
                        while True:
                            args.append(self.expr())
                            if not self.optional(","):
                                break
                    self.take(")")
                    left = ("builtin", value.upper(), args)
                else:
                    left = self.path(value)
        while self.peek() in self.precedence and self.precedence[self.peek()] >= minimum:
            operator = self.take().upper()
            level = self.precedence[operator]
            left = ("binary", operator, left, self.expr(level if operator == "**" else level + 1))
        return left

    def path(self, name):
        index, member = None, None
        if self.optional("["):
            index = self.expr()
            self.take("]")
        if self.optional("."):
            member = self.take().upper()
        return ("path", name.upper(), index, member)

    def program(self):
        if self.peek() not in {"PROGRAM", "FUNCTION_BLOCK"}:
            raise UnsupportedST("扫描模型仅支持单个PROGRAM/FB")
        self.take()
        self.take()
        declarations = []
        while self.peek() in {"VAR", "VAR_INPUT", "VAR_OUTPUT", "VAR_IN_OUT", "VAR_TEMP"}:
            self.take()
            while self.peek() != "END_VAR":
                names = [self.take().upper()]
                while self.optional(","):
                    names.append(self.take().upper())
                self.take(":")
                kind, bounds = self.take().upper(), None
                if kind == "ARRAY":
                    self.take("[")
                    low = int(self.take())
                    self.take("..")
                    high = int(self.take())
                    self.take("]")
                    self.take("OF")
                    kind, bounds = self.take().upper(), (low, high)
                    if not 0 <= high - low < 1000:
                        raise UnsupportedST("数组规模超过扫描校验上限")
                if kind not in {
                    "BOOL",
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
                    "TON",
                }:
                    raise UnsupportedST("变量类型未纳入扫描模型：" + kind)
                initial = self.expr() if self.optional(":=") else None
                self.take(";")
                declarations.extend((name, kind, bounds, initial) for name in names)
            self.take("END_VAR")
        body = self.block({"END_PROGRAM", "END_FUNCTION_BLOCK"})
        if self.peek():
            self.take()
            self.optional(";")
        if self.peek():
            raise UnsupportedST("扫描模型只处理一个POU")
        return declarations, body

    def case_label(self):
        return bool(re.fullmatch(r"\d+", self.peek())) and self.peek(1) in {":", ","}

    def block(self, stops, *, case=False):
        statements = []
        while self.peek() and self.peek() not in stops and not (case and self.case_label()):
            statements.append(self.statement())
        return statements

    def statement(self):
        if self.optional("IF"):
            condition = self.expr()
            self.take("THEN")
            branches = [(condition, self.block({"ELSIF", "ELSE", "END_IF"}))]
            while self.optional("ELSIF"):
                condition = self.expr()
                self.take("THEN")
                branches.append((condition, self.block({"ELSIF", "ELSE", "END_IF"})))
            otherwise = self.block({"END_IF"}) if self.optional("ELSE") else []
            self.take("END_IF")
            self.optional(";")  # Semantic checks can also compare an editor fragment.
            return ("if", branches, otherwise)
        if self.optional("CASE"):
            expression = self.expr()
            self.take("OF")
            branches = []
            while self.peek() not in {"ELSE", "END_CASE", ""}:
                labels = [int(self.take())]
                while self.optional(","):
                    labels.append(int(self.take()))
                self.take(":")
                branches.append((labels, self.block({"ELSE", "END_CASE"}, case=True)))
            otherwise = self.block({"END_CASE"}) if self.optional("ELSE") else []
            self.take("END_CASE")
            self.optional(";")
            return ("case", expression, branches, otherwise)
        if self.optional("FOR"):
            variable = self.take().upper()
            self.take(":=")
            start = self.expr()
            self.take("TO")
            end = self.expr()
            step = self.expr() if self.optional("BY") else ("literal", 1)
            self.take("DO")
            body = self.block({"END_FOR"})
            self.take("END_FOR")
            self.optional(";")
            return ("for", variable, start, end, step, body)
        name = self.take()
        if self.optional("("):
            args = {}
            if self.peek() != ")":
                while True:
                    key = self.take().upper()
                    self.take(":=")
                    args[key] = self.expr()
                    if not self.optional(","):
                        break
            self.take(")")
            self.take(";")
            return ("call", name.upper(), args)
        target = self.path(name)
        self.take(":=")
        expression = self.expr()
        self.take(";")
        return ("set", target, expression)


class Machine:
    def __init__(self, source):
        self.declarations, self.body = Parser(source).program()
        self.state, self.types, self.budget = {}, {}, 50000
        for name, kind, bounds, initial in self.declarations:
            self.types[name] = kind
            default = (
                False
                if kind == "BOOL"
                else {"IN": False, "Q": False, "ET": 0.0, "PT": 0.0}
                if kind == "TON"
                else 0.0
                if kind in {"REAL", "LREAL"}
                else 0
            )
            if bounds:
                self.state[name] = {index: default for index in range(bounds[0], bounds[1] + 1)}
            else:
                self.state[name] = default
            if initial is not None:
                self.assign(("path", name, None, None), self.value(initial))

    def value(self, node):
        self.budget -= 1
        if self.budget < 0:
            raise UnsupportedST("扫描计算量超过受限模型预算")
        kind = node[0]
        if kind == "literal":
            return node[1]
        if kind == "path":
            value = self.state[node[1]]
            if node[2] is not None:
                value = value[int(self.value(node[2]))]
            if node[3] is not None:
                value = value[node[3]]
            return value
        if kind == "unary":
            value = self.value(node[2])
            if node[1] == "NOT" and not isinstance(value, bool):
                raise UnsupportedST("位宽相关NOT尚未纳入扫描模型")
            return not value if node[1] == "NOT" else -value if node[1] == "-" else value
        if kind == "builtin":
            args = [self.value(arg) for arg in node[2]]
            if node[1] == "ABS" and len(args) == 1:
                return abs(args[0])
            if node[1] in {"MIN", "MAX"} and args:
                return min(args) if node[1] == "MIN" else max(args)
            raise UnsupportedST("函数不在受限扫描模型中")
        if kind == "binary":
            operator, left, right = node[1], self.value(node[2]), self.value(node[3])
            if operator == "/" and type(left) is int and type(right) is int:
                return int(left / right)
            if operator == "MOD":
                if type(left) is not int or type(right) is not int:
                    raise UnsupportedST("MOD仅支持整数")
                return left - int(left / right) * right
            if operator in {"AND", "OR", "XOR"} and type(left) is int and type(right) is int:
                return {
                    "AND": lambda: left & right,
                    "OR": lambda: left | right,
                    "XOR": lambda: left ^ right,
                }[operator]()
            operations = {
                "+": lambda: left + right,
                "-": lambda: left - right,
                "*": lambda: left * right,
                "/": lambda: left / right,
                "MOD": lambda: left % right,
                "**": lambda: left**right,
                "AND": lambda: bool(left) and bool(right),
                "OR": lambda: bool(left) or bool(right),
                "XOR": lambda: bool(left) != bool(right),
                "=": lambda: left == right,
                "<>": lambda: left != right,
                "<": lambda: left < right,
                ">": lambda: left > right,
                "<=": lambda: left <= right,
                ">=": lambda: left >= right,
            }
            return operations[operator]()
        raise UnsupportedST("不支持的表达式")

    def assign(self, target, value):
        name, index, member = target[1:]
        if name not in self.state:
            raise UnsupportedST("未解析的外部变量")
        kind = self.types[name]
        if isinstance(value, (int, float)) and not math.isfinite(value):
            raise UnsupportedST("非有限数值不在扫描模型范围")
        if kind in {"REAL", "LREAL"}:
            value = (
                struct.unpack("f", struct.pack("f", float(value)))[0]
                if kind == "REAL"
                else float(value)
            )
        elif kind == "BOOL":
            value = bool(value)
        elif kind != "TON":
            value = int(value)
            bounds = {
                "SINT": (-128, 127),
                "INT": (-32768, 32767),
                "DINT": (-2147483648, 2147483647),
                "USINT": (0, 255),
                "UINT": (0, 65535),
                "UDINT": (0, 4294967295),
            }
            if kind in bounds and not bounds[kind][0] <= value <= bounds[kind][1]:
                raise UnsupportedST("整数溢出行为依赖目标PLC")
        if member is not None:
            raise UnsupportedST("不能写入FB内部状态")
        if index is not None:
            slot = int(self.value(index))
            if slot not in self.state[name]:
                raise UnsupportedST("数组越界")
            self.state[name][slot] = value
        else:
            self.state[name] = value

    def execute(self, body):
        for statement in body:
            self.budget -= 1
            if self.budget < 0:
                raise UnsupportedST("扫描计算量超过受限模型预算")
            kind = statement[0]
            if kind == "set":
                self.assign(statement[1], self.value(statement[2]))
            elif kind == "if":
                for condition, branch in statement[1]:
                    if self.value(condition):
                        self.execute(branch)
                        break
                else:
                    self.execute(statement[2])
            elif kind == "case":
                value = self.value(statement[1])
                for labels, branch in statement[2]:
                    if value in labels:
                        self.execute(branch)
                        break
                else:
                    self.execute(statement[3])
            elif kind == "for":
                _, name, start, end, step, branch = statement
                start, end, step = (
                    int(self.value(start)),
                    int(self.value(end)),
                    int(self.value(step)),
                )
                if not step or abs(end - start) // abs(step) > 1000:
                    raise UnsupportedST("循环超过受限模型范围")
                self.assign(("path", name, None, None), start)
                while self.state[name] <= end if step > 0 else self.state[name] >= end:
                    self.execute(branch)
                    self.assign(("path", name, None, None), self.state[name] + step)
            elif kind == "call":
                _, name, args = statement
                if self.types.get(name) != "TON":
                    raise UnsupportedST("外部调用不在受限扫描模型中")
                timer = self.state[name]
                for key, expression in args.items():
                    if key not in {"IN", "PT"}:
                        raise UnsupportedST("TON参数不受支持")
                    timer[key] = self.value(expression)
                timer["ET"] = 0.0
                timer["Q"] = bool(timer["IN"]) and timer["PT"] <= 0
            else:
                raise UnsupportedST("不支持的语句")

    def external_path(self, value):
        parser = Parser(value)
        node = parser.path(parser.take())
        if parser.peek():
            raise UnsupportedST("变量路径无效")
        return node

    def scan(self):
        self.budget = 50000
        self.execute(self.body)


def verify_scans(source, scenarios):
    """Reject a false expected value; never silently rewrite model assertions."""
    count = 0
    mismatches = []

    def reject_mismatches():
        if mismatches:
            raise DomainError(
                "ST_OUTPUT_INVALID",
                "；".join(mismatches[:8])
                + "。请逐项核对场景输入、源码运算和预期常量，不要改被测程序、删除断言或放大容差。",
                503,
            )

    try:
        for case in scenarios:
            if case.execution != "st":
                continue
            machine = Machine(source)
            for index, scan in enumerate(case.scans, 1):
                for assignment in scan.assignments:
                    machine.assign(
                        machine.external_path(assignment.target), literal(assignment.value)
                    )
                machine.scan()
                for assertion in scan.assertions:
                    actual = machine.value(machine.external_path(assertion.target))
                    expected = literal(assertion.expected)
                    matches = (
                        abs(actual - expected) <= assertion.tolerance
                        if assertion.tolerance is not None
                        else actual == expected
                    )
                    if not matches:
                        mismatches.append(
                            f"{case.id} 扫描{index}的预期与代码语义不符："
                            f"{assertion.target} 计算值为 {actual}，提供的预期为 {assertion.expected}"
                        )
                count += 1
        reject_mismatches()
        return f"已用受限零耗时扫描模型核对 {count} 个逻辑扫描步骤；这不等同于目标 PLC 编译或执行，真实定时器仍需人工验证。"
    except (ValueError, KeyError, TypeError, IndexError, OverflowError, RecursionError):
        reject_mismatches()
        return "输入包含受限扫描模型尚未覆盖的类型、调用或语法，未做本地语义执行；需目标 PLC 工程验证。"
    except ZeroDivisionError:
        raise DomainError(
            "ST_OUTPUT_INVALID",
            "扫描用例会触发除零，不能声称获得普通数值预期；请复核输入与修复要求。",
            503,
        ) from None
