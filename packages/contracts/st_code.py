"""Contracts for the platform-managed Structured Text specialist."""

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, model_validator

from .common import ID, Envelope, Model, Time

STAction = Literal["comment", "test", "review", "fix"]
STText = Annotated[str, StringConstraints(min_length=1, max_length=6000)]
STCode = Annotated[str, StringConstraints(min_length=1, max_length=30000)]


class STStart(Envelope):
    retry_from: ID | None = None
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)] = (
        "ST 代码优化"
    )
    filename: Annotated[
        str, StringConstraints(pattern=r"^[^/\\\x00-\x1f]{1,100}\.(?i:st|scl|txt)$")
    ] = "source.st"
    code: Annotated[str, StringConstraints(min_length=1, max_length=12000)]
    dialect: Literal["generic", "codesys", "siemens", "twincat"] = "generic"
    instructions: Annotated[str, StringConstraints(max_length=2000)] = ""
    actions: Annotated[list[STAction], Field(min_length=1, max_length=4)] = [
        "comment",
        "test",
        "review",
        "fix",
    ]

    @model_validator(mode="after")
    def valid_source(self):
        if not self.code.strip() or "\x00" in self.code or len(self.code.splitlines()) > 600:
            raise ValueError("请输入非空 ST 文本，最多 12000 字符、600 行，不得包含 NUL")
        if len(self.actions) != len(set(self.actions)):
            raise ValueError("操作不能重复")
        return self


class STBug(Model):
    id: Annotated[str, StringConstraints(pattern=r"^B[0-9]{3}$")]
    title: STText
    severity: Literal["high", "medium", "low"]
    confidence: Literal["confirmed", "possible"]
    line_start: Annotated[int, Field(ge=1)]
    line_end: Annotated[int, Field(ge=1)]
    evidence: STText
    explanation: STText
    suggestion: STText
    disposition: Literal["fixed", "not_fixed", "needs_context"]
    fix_explanation: STText


class STTestCase(Model):
    id: Annotated[str, StringConstraints(pattern=r"^T[0-9]{3}$")]
    title: STText
    category: Literal["normal", "boundary", "abnormal", "regression", "scan_cycle"]
    preconditions: STText
    steps: Annotated[list[STText], Field(min_length=1, max_length=20)]
    expected: STText
    related_bug_ids: Annotated[list[str], Field(max_length=30)]
    execution: Literal["st", "manual"] = "st"
    adaptation_note: Annotated[str, StringConstraints(max_length=2000)] = ""


class STResult(Model):
    summary: STText
    annotated_code: STCode | None
    test_code: STCode | None
    tests: Annotated[list[STTestCase], Field(max_length=40)]
    bugs: Annotated[list[STBug], Field(max_length=30)]
    fixed_code: STCode | None
    assumptions: Annotated[list[STText], Field(max_length=20)]
    scan_verification: Literal["passed", "not_supported", "not_run"] = "not_run"
    scan_verification_note: str = ""


class STTestDeclaration(Model):
    name: Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z_0-9]{0,79}$")]
    type: Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z_0-9.]{0,99}$")]


class STTestCheck(Model):
    test_id: Annotated[str, StringConstraints(pattern=r"^T[0-9]{3}$")]
    setup: Annotated[str, StringConstraints(max_length=4000)] = ""
    actual: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    expected: Annotated[str, StringConstraints(min_length=1, max_length=1000)]
    tolerance: Annotated[float, Field(ge=0, le=1e12)] | None = None


class STFinding(Model):
    title: STText
    severity: Literal["high", "medium", "low"]
    confidence: Literal["confirmed", "possible"]
    line_start: Annotated[int, Field(ge=1)]
    line_end: Annotated[int, Field(ge=1)]
    explanation: STText
    suggestion: STText


class STStageMeta(Model):
    source_line_count: Annotated[int, Field(ge=1)]
    source_last_line: Annotated[str, StringConstraints(max_length=12000)]


class STReviewStage(STStageMeta):
    summary: STText
    findings: Annotated[list[STFinding], Field(max_length=12)]
    assumptions: Annotated[list[STText], Field(max_length=12)]


class STLineComment(Model):
    line: Annotated[int, Field(ge=1)]
    text: Annotated[str, StringConstraints(min_length=1, max_length=500)]


class STAnnotationStage(STStageMeta):
    comments: Annotated[list[STLineComment], Field(min_length=1, max_length=100)]


class STSourceEdit(Model):
    line_start: Annotated[int, Field(ge=1)]
    line_end: Annotated[int, Field(ge=1)]
    replacement: Annotated[str, StringConstraints(max_length=12000)]
    bug_ids: Annotated[list[str], Field(min_length=1, max_length=12)]
    explanation: STText


class STRepairStage(STStageMeta):
    edits: Annotated[list[STSourceEdit], Field(max_length=40)]


class STTestsStage(STStageMeta):
    tests: Annotated[list[STTestCase], Field(min_length=1, max_length=20)]
    declarations: Annotated[list[STTestDeclaration], Field(max_length=50)]
    checks: Annotated[list[STTestCheck], Field(max_length=100)]


class STScanAssignment(Model):
    target: Annotated[
        str,
        StringConstraints(
            pattern=r"^[A-Za-z_][A-Za-z_0-9]*(?:\[[0-9]+\])?(?:\.[A-Za-z_][A-Za-z_0-9]*)?$"
        ),
    ]
    value: Annotated[str, StringConstraints(min_length=1, max_length=100)]


class STScanAssertion(Model):
    target: Annotated[
        str,
        StringConstraints(
            pattern=r"^[A-Za-z_][A-Za-z_0-9]*(?:\[[0-9]+\])?(?:\.[A-Za-z_][A-Za-z_0-9]*)?$"
        ),
    ]
    expected: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    tolerance: Annotated[float, Field(ge=0, le=1e12)] | None


class STScan(Model):
    assignments: Annotated[list[STScanAssignment], Field(max_length=30)]
    assertions: Annotated[list[STScanAssertion], Field(min_length=1, max_length=6)]


class STScenario(Model):
    id: Annotated[str, StringConstraints(pattern=r"^T[0-9]{3}$")]
    title: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    category: Literal["normal", "boundary", "abnormal", "regression", "scan_cycle"]
    related_bug_ids: Annotated[list[str], Field(max_length=30)]
    execution: Literal["st", "manual"]
    scans: Annotated[list[STScan], Field(max_length=8)]
    manual_preconditions: Annotated[str, StringConstraints(max_length=800)]
    manual_steps: Annotated[
        list[Annotated[str, StringConstraints(min_length=1, max_length=800)]], Field(max_length=8)
    ]
    manual_expected: Annotated[str, StringConstraints(max_length=800)]
    adaptation_note: Annotated[str, StringConstraints(max_length=1000)]


class STScenariosStage(STStageMeta):
    scenarios: Annotated[list[STScenario], Field(min_length=1, max_length=8)]


class STChecks(Model):
    annotation_tokens_preserved: bool | None
    fixed_lexically_valid: bool | None
    compiler: Literal["not_run"] = "not_run"
    tests: Literal["not_run"] = "not_run"
    scan_verification: Literal["passed", "not_supported", "not_run"] = "not_run"
    note: str = "已检查输出结构、原文引用和 ST 基础结构（结束符、声明、占位符与辅助调用）；未调用 PLC 编译器，未在目标 PLC 执行测试。"


class STArtifact(Model):
    name: str
    download_url: str


class STRun(Envelope):
    retry_of: ID | None = None
    reused_stages: list[str] = []
    run_id: ID
    status: Literal["queued", "running", "completed", "failed", "interrupted", "cancelled"]
    created_at: Time
    updated_at: Time
    request: STStart
    source_sha256: str
    workflow_version: Literal["st-code-v1"] = "st-code-v1"
    model_id: str
    session_id: str | None = None
    attempt_count: int = 0
    validation_feedback: list[str] = []
    stage: Literal["review", "annotation", "repair", "tests", "validation"] | None = None
    stage_sessions: dict[str, list[str]] = {}
    stage_requests: dict[str, list[str]] = {}
    stage_results: dict[str, dict] = {}
    result: STResult | None = None
    checks: STChecks | None = None
    artifacts: list[STArtifact] = []
    error: str | None = None


class STRunSummary(Envelope):
    run_id: ID
    title: str
    filename: str
    status: str
    created_at: Time


class STRuns(Envelope):
    items: list[STRunSummary]
