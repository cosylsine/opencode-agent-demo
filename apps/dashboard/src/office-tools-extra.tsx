import React from "react";

export const toolGroups: Record<string, string> = {
  read_document: "文档与 PDF", generate_document: "文档与 PDF", process_pdf: "文档与 PDF",
  read_table: "表格与数据", transform_table: "表格与数据", validate_table: "表格与数据",
  generate_spreadsheet: "表格与数据", merge_tables: "表格与数据", clean_table: "表格与数据",
  process_text: "文本与隐私", compare_documents: "文本与隐私", redact_text: "文本与隐私", export_text: "文本与隐私",
  calculate: "计算与文件", date_calculate: "计算与文件", workday_calculate: "计算与文件", bundle_files: "计算与文件",
};

type Props = {
  selected: string;
  values: Record<string, unknown>;
  update: (key: string, value: unknown) => void;
  fileField: (key: string, required?: boolean) => React.ReactNode;
  text: (key: string, placeholder?: string, multiline?: boolean) => React.ReactNode;
  multipleFiles: React.ReactNode;
};

export function ExtraToolFields({ selected, values, update, fileField, text, multipleFiles }: Props) {
  function choice(key: string, label: string, initial: string, options: string[][]) {
    return <label>{label}<select value={String(values[key] ?? initial)} onChange={e => update(key, e.target.value)}>
      {options.map(([value, title]) => <option key={value} value={value}>{title}</option>)}
    </select></label>;
  }
  function check(key: string, label: string, initial = false) {
    return <label className="of-check"><input type="checkbox" checked={Boolean(values[key] ?? initial)} onChange={e => update(key, e.target.checked)} />{label}</label>;
  }
  function source(prefix = "", title = "输入内容") {
    const mode = String(values[prefix + "source_mode"] ?? "text");
    return <fieldset className="of-tool-source"><legend>{title}</legend>
      {choice(prefix + "source_mode", "输入方式", "text", [["text", "粘贴文本"], ["file", "选择文档"]])}
      {mode === "text" ? text(prefix + "content", "填写文本内容", true) : fileField(prefix + "file_id", true)}
    </fieldset>;
  }
  if (selected === "process_pdf") return <>
    {choice("operation", "页面操作", "merge", [["merge", "按顺序合并 PDF"], ["extract", "提取页面"], ["rotate", "旋转页面"]])}
    {multipleFiles}
    {values.operation !== undefined && values.operation !== "merge" && text("pages", "例如：1-3,5（留空为全部页面）")}
    {values.operation === "rotate" && choice("rotation", "顺时针旋转", "90", [["90", "90°"], ["180", "180°"], ["270", "270°"]])}
    {text("filename", "整理结果.pdf")}
  </>;
  if (selected === "merge_tables") return <>
    {multipleFiles}
    {choice("alignment", "字段对齐方式", "strict", [["strict", "严格匹配字段（顺序可不同）"], ["union", "按字段名取并集，缺少的值留空"]])}
    <label>来源列名称（留空不添加）<input value={String(values.source_column ?? "来源文件")} onChange={e => update("source_column", e.target.value)} /></label>
    {text("filename", "合并表格.xlsx（也支持 .csv）")}
  </>;
  if (selected === "clean_table") return <>
    {fileField("file_id", true)}{text("sheet")}{text("columns", "需要清洗的列名，以逗号分隔；留空为全部列")}
    {check("trim", "清除文本首尾空白", true)}{check("normalize_width", "统一全角字符与兼容字符")}
    {text("filename", "清洗结果.xlsx（改为 .csv 可转换格式）")}
  </>;
  if (selected === "process_text") return <>
    {source()}
    {choice("operation", "整理方式", "clean", [["clean", "清理首尾空白与多余空行"], ["deduplicate", "按行去重（同时清理空白与空行）"], ["statistics", "统计字符、汉字、英文单词与行数"]])}
    {values.operation !== "statistics" && check("normalize_width", "统一全角字符与兼容字符")}
    {text("filename", "文本整理.txt（也支持 .md）")}
  </>;
  if (selected === "compare_documents") return <>
    {source("", "旧版")}{source("other_", "新版")}{text("filename", "文稿差异.txt")}
  </>;
  if (selected === "redact_text") return <>
    {source()}
    <fieldset className="of-tool-source"><legend>需要遮盖的信息</legend>
      {[["phone", "大陆手机号"], ["id_card", "18 位身份证号"], ["email", "邮箱"], ["bank_card", "16–19 位银行卡号"]].map(([key, label]) => {
        const categories = (values.categories ?? ["phone", "id_card", "email"]) as string[];
        return <label className="of-check" key={key}><input type="checkbox" checked={categories.includes(key)} onChange={e => update("categories", e.target.checked ? [...categories, key] : categories.filter(c => c !== key))} />{label}</label>;
      })}
    </fieldset>
    {text("custom_terms", "每行一个需要遮盖的姓名、单位或其他词语", true)}
    <p className="of-muted">规则匹配可能漏报或误报，分享前请人工复核。输出为纯文本。</p>
    {text("filename", "脱敏文本.txt")}
  </>;
  if (selected === "workday_calculate") return <>
    {choice("operation", "计算方式", "difference", [["difference", "两个日期相差的工作日"], ["add_days", "增加／减少工作日，计算截止日期"]])}
    <label>起始日期<input type="date" required value={String(values.date ?? "")} onChange={e => update("date", e.target.value)} /></label>
    {(values.operation ?? "difference") === "difference" ? <label>结束日期<input type="date" required value={String(values.other_date ?? "")} onChange={e => update("other_date", e.target.value)} /></label> : <label>增加工作日数<input type="number" min={-10000} max={10000} value={Number(values.days ?? 0)} onChange={e => update("days", Number(e.target.value))} /></label>}
    {text("holidays", "例如：2026-10-01，每行一个日期", true)}
    {text("working_dates", "例如：2026-10-10，每行一个日期", true)}
    <p className="of-muted">以周一至周五为基础，不含起始日、包含结束日。请自行填写放假与补班日期。</p>
  </>;
  if (selected === "bundle_files") return <>{multipleFiles}{text("filename", "办公材料.zip")}</>;
  return null;
}
