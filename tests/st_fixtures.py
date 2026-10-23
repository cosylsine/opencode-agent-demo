"""Deterministic ST protocol fixtures, used only by explicitly injected test clients."""

from packages.contracts.st_code import STResult

SOURCE = """FUNCTION_BLOCK FB_Ratio
VAR_INPUT
    Enable : BOOL;
    Numerator : REAL;
    Denominator : REAL;
END_VAR
VAR_OUTPUT
    Ratio : REAL;
    Valid : BOOL;
END_VAR

IF Enable THEN
    Ratio := Numerator / Denominator;
    Valid := TRUE;
END_IF;
END_FUNCTION_BLOCK
"""
FIXED = SOURCE.replace("IF Enable THEN", "Ratio := 0.0;\nValid := FALSE;\nIF Enable AND (Denominator <> 0.0) THEN")


def result_for(request):
    examine = "review" in request.actions or "fix" in request.actions
    known = request.code == SOURCE
    fix = "fix" in request.actions and known
    bugs = []
    if examine and known:
        for number, title, line, evidence in [
            (1, "除数为零", 13, "Ratio := Numerator / Denominator;"),
            (2, "禁用后输出保持", 12, "IF Enable THEN"),
        ]:
            bugs.append(dict(id=f"B{number:03}", title=title, severity="high", confidence="confirmed",
                             line_start=line, line_end=line, evidence=evidence,
                             explanation="该输入会造成无效结果或保留上周期状态。", suggestion="按明确的输出约定增加防护。",
                             disposition="fixed" if fix else "not_fixed",
                             fix_explanation="已按测试约定在禁用或零除数时复位输出。" if fix else "本次仅检查代码。"))
    tests = []
    if "test" in request.actions:
        for i, category in enumerate(("normal", "boundary", "abnormal", "scan_cycle", "regression"), 1):
            tests.append(dict(id=f"T{i:03}", title="比值功能块" + category, category=category,
                              preconditions="独立实例，扫描周期 100 ms，输出初始为 0 和 FALSE。",
                              steps=["Enable=TRUE、Numerator=10、Denominator=2，调用一个扫描周期。",
                                     "将 Denominator=0 后调用，再将 Enable=FALSE 调用。"],
                              expected="正常时 Ratio=5、Valid=TRUE；零除数或禁用时 Ratio=0、Valid=FALSE。",
                              related_bug_ids=[b["id"] for b in bugs] if category == "regression" else []))
    return STResult(summary="测试协议结果：已完成所选 ST 代码操作，测试未执行。",
                    annotated_code="(* 测试注释：保持原逻辑 *)\n" + request.code if "comment" in request.actions else None,
                    test_code="PROGRAM TestRatio\nVAR\nSut : FB_Ratio;\nTestPassed : BOOL;\nEND_VAR\nSut(Enable := TRUE, Numerator := 10.0, Denominator := 2.0);\nTestPassed := (Sut.Ratio = 5.0) AND Sut.Valid;\nEND_PROGRAM" if tests else None,
                    tests=tests, bugs=bugs, fixed_code=FIXED if fix else request.code if "fix" in request.actions else None,
                    assumptions=["未运行 PLC 编译器和测试；测试中约定无效输入时输出复位。"])
