"""Typed, deterministic office tools shared by forms and the MCP dispatcher."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from packages.contracts.common import Model


class ReadDocument(Model):
    file_id: str
    start: Annotated[int, Field(ge=0)] = 0
    limit: Annotated[int, Field(ge=1, le=16000)] = 8000


class ReadTable(Model):
    file_id: str
    sheet: str | None = None
    columns: list[str] = []
    offset: Annotated[int, Field(ge=0)] = 0
    limit: Annotated[int, Field(ge=1, le=100)] = 30


class Filter(Model):
    column: str
    operator: Literal["eq", "ne", "gt", "ge", "lt", "le", "contains", "empty"] = "eq"
    value: str | float | int | None = None


class TransformTable(Model):
    file_id: str
    sheet: str | None = None
    operation: Literal["filter", "sort", "group", "deduplicate", "join", "compare"]
    columns: list[str] = []
    filters: list[Filter] = []
    aggregations: dict[str, Literal["sum", "mean", "min", "max", "count"]] = {}
    descending: bool = False
    other_file_id: str | None = None
    filename: str = "处理结果.xlsx"


class Rule(Model):
    column: str
    required: bool = False
    type: Literal["text", "number", "date"] | None = None
    minimum: float | None = None
    maximum: float | None = None
    unique: bool = False


class ValidateTable(Model):
    file_id: str
    sheet: str | None = None
    rules: list[Rule]


class Calculate(Model):
    expression: Annotated[str, Field(min_length=1, max_length=2000)]


class DateCalculate(Model):
    operation: Literal["difference", "add_days"]
    date: str
    other_date: str | None = None
    days: Annotated[int, Field(ge=-365000, le=365000)] = 0


class Section(Model):
    kind: Literal["heading", "paragraph", "list", "table"]
    text: str = ""
    level: Annotated[int, Field(ge=1, le=3)] = 1
    items: list[str] = []
    rows: list[list[str | float | int | None]] = []


class GenerateDocument(Model):
    title: Annotated[str, Field(max_length=200)] = "文档"
    content: Annotated[str, Field(max_length=500000)] = Field(
        default="",
        description="要实际写入 Word 的完整正文，支持纯文本及 Markdown 标题、列表和表格。",
    )
    paragraphs: list[str] = []
    sections: list[Section] = []
    table_file_id: str | None = None
    template_file_id: str | None = None
    filename: str = "生成文档.docx"

    @model_validator(mode="after")
    def require_body(self):
        sections_have_text = any(
            (s.kind in ("heading", "paragraph") and s.text.strip())
            or (s.kind == "list" and any(item.strip() for item in s.items))
            or (
                s.kind == "table"
                and any(value is not None and str(value).strip() for row in s.rows for value in row)
            )
            for s in self.sections
        )
        if not (
            self.content.strip()
            or any(p.strip() for p in self.paragraphs)
            or sections_have_text
            or self.table_file_id
            or self.template_file_id
        ):
            raise ValueError(
                "Word 正文不能为空：请提供 content、paragraphs、sections 或有效附表／模板；标题不算正文"
            )
        return self


class GenerateSpreadsheet(Model):
    headers: list[str]
    rows: list[list[str | float | int | None]]
    filename: str = "生成表格.xlsx"


class ExportText(Model):
    content: Annotated[str, Field(max_length=500000)]
    filename: str = "结果.md"


FileIds = Annotated[list[str], Field(min_length=1, max_length=20)]


class ProcessPDF(Model):
    file_ids: FileIds
    operation: Literal["merge", "extract", "rotate"] = "merge"
    pages: Annotated[str, Field(max_length=5000)] = Field(
        default="",
        description="页码从 1 开始，如 1-3,5；留空表示全部。提取/旋转只接受一份 PDF。合并按 file_ids 顺序。",
    )
    rotation: Literal[90, 180, 270] = 90
    filename: str = "整理结果.pdf"


class MergeTables(Model):
    file_ids: Annotated[list[str], Field(min_length=2, max_length=20)]
    alignment: Literal["strict", "union"] = "strict"
    source_column: str = "来源文件"
    filename: str = "合并表格.xlsx"


class CleanTable(Model):
    file_id: str
    sheet: str | None = None
    columns: list[str] = []
    trim: bool = True
    normalize_width: bool = False
    filename: str = "清洗结果.xlsx"


class TextInput(Model):
    content: Annotated[str, Field(max_length=100000)] = ""
    file_id: str | None = None

    @model_validator(mode="after")
    def require_source(self):
        if bool(self.content) == bool(self.file_id):
            raise ValueError("请填写文本或选择一份文档，二者选一")
        return self


class ProcessText(TextInput):
    operation: Literal["clean", "deduplicate", "statistics"] = "clean"
    normalize_width: bool = False
    filename: str = "文本整理.txt"


class CompareDocuments(TextInput):
    other_content: Annotated[str, Field(max_length=50000)] = ""
    other_file_id: str | None = None
    filename: str = "文稿差异.txt"

    @model_validator(mode="after")
    def require_other(self):
        if bool(self.other_content) == bool(self.other_file_id):
            raise ValueError("请填写新版文本或选择新版文档，二者选一")
        return self


class RedactText(TextInput):
    categories: list[Literal["phone", "id_card", "email", "bank_card"]] = [
        "phone",
        "id_card",
        "email",
    ]
    custom_terms: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=200)]], Field(max_length=100)
    ] = []
    filename: str = "脱敏文本.txt"


class WorkdayCalculate(Model):
    operation: Literal["difference", "add_days"] = "difference"
    date: str
    other_date: str | None = None
    days: Annotated[int, Field(ge=-10000, le=10000)] = 0
    holidays: Annotated[list[str], Field(max_length=3660)] = []
    working_dates: Annotated[list[str], Field(max_length=3660)] = []


class BundleFiles(Model):
    file_ids: FileIds
    filename: str = "办公材料.zip"


TOOL_REGISTRY = {
    "read_document": (
        "读取文档",
        "分段读取 DOCX、TXT、Markdown、文本型 PDF；start 是字符偏移。",
        ReadDocument,
    ),
    "read_table": (
        "读取表格",
        "读取 XLSX/CSV 工作表、字段与指定行；返回总行数及分页信息。",
        ReadTable,
    ),
    "transform_table": (
        "处理表格",
        "对完整表格进行筛选、排序、分组统计、去重、匹配或差异比较，输出 XLSX。分组时 columns 为分组列，aggregations 为统计列到统计方法的映射。",
        TransformTable,
    ),
    "validate_table": (
        "校验表格",
        "检查必填、类型、数值范围和唯一性；返回问题清单。",
        ValidateTable,
    ),
    "calculate": (
        "数值与统计计算",
        "计算受限表达式，支持算术及 sum、mean、min、max、round、abs，不执行程序代码。",
        Calculate,
    ),
    "date_calculate": (
        "日期计算",
        "计算两个 ISO 日期的相差天数，或给日期增加指定天数。",
        DateCalculate,
    ),
    "generate_document": (
        "生成 Word",
        "生成 Word（DOCX）。必须用 content 传入完整正文，或用 paragraphs/sections 传入正文块；标题不能代替正文。content 支持纯文本及 Markdown 标题、列表和表格。不要只在最终对话中写正文。可附表或使用 DOCX 模板；导出 Markdown 请用 export_text。",
        GenerateDocument,
    ),
    "generate_spreadsheet": (
        "生成表格",
        "根据 headers 和 rows 生成 XLSX 或 CSV；数据较大时使用 transform_table。",
        GenerateSpreadsheet,
    ),
    "export_text": ("导出文本", "将内容保存为 TXT 或 Markdown 文件，返回可下载文件。", ExportText),
    "process_pdf": (
        "PDF 合并与页面整理",
        "合并多份 PDF，或对一份 PDF 提取指定页面、旋转页面。支持扫描件页面操作，不提供 OCR；不接受加密 PDF。",
        ProcessPDF,
    ),
    "merge_tables": (
        "批量合并表格",
        "按所选顺序纵向合并 2–20 份 XLSX/CSV 的首张工作表，严格匹配字段或按字段名取并集；可增加来源列，输出 XLSX/CSV。",
        MergeTables,
    ),
    "clean_table": (
        "表格清洗与转换",
        "清理指定列或全部列的文本首尾空白，可统一全角字符；用输出文件名选择 XLSX 或 CSV。按值导出，不保留原样式或公式。",
        CleanTable,
    ),
    "process_text": (
        "文本整理与统计",
        "粘贴文本或选择文档，清理空白、按行去重或统计字数；输出 TXT/Markdown。统计不改原文，去重保留首次出现顺序。",
        ProcessText,
    ),
    "compare_documents": (
        "文稿版本对比",
        "对比两段文本或 DOCX/TXT/Markdown/文本型 PDF，按行标记新增和删除，导出差异 TXT。每版最多 50000 字符；只比较文本内容。",
        CompareDocuments,
    ),
    "redact_text": (
        "常见信息脱敏",
        "按格式遮盖大陆手机号、18 位身份证号、邮箱、16–19 位银行卡号及指定词语，导出纯文本。规则匹配可能漏报或误报，请人工复核。",
        RedactText,
    ),
    "workday_calculate": (
        "工作日计算",
        "按周一至周五计算工作日差或截止日期；可手动提供放假日期和补班日期。不含内置节假日日历；区间不含起始日、包含结束日。",
        WorkdayCalculate,
    ),
    "bundle_files": (
        "文件打包",
        "将 1–20 份当前任务可访问的办公文件按原文件名打包为 ZIP，同名自动编号；不解压输入文件。",
        BundleFiles,
    ),
}


def validate_arguments(tool, arguments):
    from pydantic import ValidationError

    from packages.domain.core import DomainError

    if tool not in TOOL_REGISTRY:
        raise DomainError("UNKNOWN_TOOL", "工具不存在", 404)
    try:
        return TOOL_REGISTRY[tool][2].model_validate(arguments).model_dump(mode="json")
    except ValidationError as exc:
        fields = ", ".join(
            ".".join(map(str, e["loc"])) or e["msg"].removeprefix("Value error, ")
            for e in exc.errors()[:8]
        )
        raise DomainError("INVALID_TOOL_ARGUMENTS", "请检查工具参数：" + fields, 422) from exc
