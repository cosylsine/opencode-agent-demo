import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { ExtraToolFields } from "./office-tools-extra";
import { ToolCatalog } from "./office-tool-catalog";
import {
  officeApi,
  FileCards,
  executionLabels,
  terminal,
  type Execution,
  type OfficeFile,
  type Tool,
} from "./office-api";

export function ExecutionCard({
  execution,
  onCancel,
}: {
  execution: Execution;
  onCancel?: () => void;
}) {
  const result = execution.result ?? {};
  return (
    <section className="of-execution" aria-live="polite">
      <div className="of-row">
        <strong>{executionLabels[execution.status]}</strong>
        {!terminal.has(execution.status) && onCancel && (
          <button onClick={onCancel}>取消任务</button>
        )}
      </div>
      {execution.error && (
        <p className="error" role="alert">
          {execution.error}
        </p>
      )}
      {(execution.kind === "tool" || execution.status !== "succeeded") && (
        <FileCards files={execution.files} />
      )}
      {typeof result.value === "number" && (
        <div className="of-number">{result.value}</div>
      )}
      {typeof result.days === "number" && <p>相差 {result.days} 天</p>}
      {typeof result.date === "string" && <p>计算结果：{result.date}</p>}
      {typeof result.text === "string" && (
        <pre className="of-text-preview">{result.text}</pre>
      )}
      {typeof result.issue_count === "number" && (
        <p>
          {result.valid ? "校验通过" : "发现 " + result.issue_count + " 项问题"}
        </p>
      )}
      {Array.isArray(result.issues) && result.issues.length > 0 && (
        <div className="of-table-scroll">
          <table>
            <thead>
              <tr>
                <th>行号</th>
                <th>字段</th>
                <th>问题</th>
              </tr>
            </thead>
            <tbody>
              {(
                result.issues as {
                  row: number;
                  column: string;
                  issue: string;
                }[]
              ).map((r, i) => (
                <tr key={i}>
                  <td>{r.row}</td>
                  <td>{r.column}</td>
                  <td>{r.issue}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {Array.isArray(result.headers) && Array.isArray(result.rows) && (
        <>
          <p>共 {String(result.total_rows)} 行，以下为本次返回内容</p>
          <div className="of-table-scroll">
            <table>
              <thead>
                <tr>
                  {(result.headers as string[]).map((h) => (
                    <th key={h}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(result.rows as Record<string, unknown>[]).map((r, i) => (
                  <tr key={i}>
                    {(result.headers as string[]).map((h) => (
                      <td key={h}>{String(r[h] ?? "")}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
      {Array.isArray(result.warnings) &&
        (result.warnings as string[]).map((w) => (
          <p className="of-muted" key={w}>
            {w}
          </p>
        ))}
      <details>
        <summary>查看执行记录</summary>
        <ol className="of-events">
          {execution.events.map((event, i) => (
            <li key={i}>
              <time>{new Date(String(event.time)).toLocaleTimeString()}</time>
              {String(event.label)}
            </li>
          ))}
        </ol>
      </details>
    </section>
  );
}

const labels: Record<string, string> = {
  file_id: "输入文件",
  other_file_id: "另一份表格",
  template_file_id: "Word 模板（可选）",
  table_file_id: "附加表格（可选）",
  sheet: "工作表名称（留空使用首张）",
  offset: "起始行（从 0 开始）",
  limit: "读取数量",
  start: "起始字符（从 0 开始）",
  filename: "输出文件名",
  title: "文档标题",
  content: "文本内容",
  expression: "计算表达式",
  date: "起始日期",
  other_date: "结束日期",
  days: "增加天数",
  columns: "分组／排序／匹配字段",
  headers: "表头字段",
  other_content: "新版文本",
  pages: "页码范围",
  custom_terms: "指定词语（可选）",
  holidays: "放假日期（可选）",
  working_dates: "补班日期（可选）",
};

export function ToolWorkspace({
  tools,
  files,
  onFiles,
  isAdmin,
  onRefresh,
}: {
  tools: Tool[];
  files: OfficeFile[];
  onFiles: (files: OfficeFile[]) => void;
  isAdmin: boolean;
  onRefresh: () => void;
}) {
  const [selected, setSelected] = useState(tools[0]?.id ?? "read_document"),
    [values, setValues] = useState<Record<string, unknown>>({});
  const [search, setSearch] = useState("");
  const [catalog, setCatalog] = useState(true), [group, setGroup] = useState("全部");
  const workspace = useRef<HTMLDivElement>(null), catalogScroll = useRef(0), hasOpened = useRef(false);
  const [execution, setExecution] = useState<Execution | null>(null),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const tool = tools.find((t) => t.id === selected);
  const update = (key: string, value: unknown) =>
    setValues((old) => ({ ...old, [key]: value }));
  useLayoutEffect(() => {
    if (!hasOpened.current) return;
    const pane = workspace.current?.closest(".of-content");
    if (pane) pane.scrollTop = catalog ? catalogScroll.current : 0;
    const focus = catalog
      ? workspace.current?.querySelector<HTMLButtonElement>(`[data-tool-id="${selected}"] button`)
      : workspace.current?.querySelector<HTMLHeadingElement>(".of-tool-body h2");
    focus?.focus({ preventScroll: true });
  }, [catalog, selected]);
  function openTool(id: string) {
    catalogScroll.current = workspace.current?.closest(".of-content")?.scrollTop ?? 0;
    hasOpened.current = true;
    setSelected(id);
    setCatalog(false);
  }
  useEffect(() => {
    setValues({});
    setExecution(null);
    setError("");
  }, [selected]);
  useEffect(() => {
    if (!execution || terminal.has(execution.status)) return;
    let active = true;
    const timer = window.setInterval(() => {
      officeApi<Execution>("/executions/" + execution.id)
        .then((value) => {
          if (active) {
            setExecution(value);
            if (terminal.has(value.status)) onFiles(value.files);
          }
        })
        .catch((e) => active && setError((e as Error).message));
    }, 1000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [execution?.id, execution?.status, onFiles]);
  async function upload(batch: File[], key: string) {
    setBusy(true);
    setError("");
    try {
      for (const file of batch) {
        const data = new FormData();
        data.append("file", file);
        const uploaded = await officeApi<OfficeFile>("/files", data);
        onFiles([uploaded]);
        if (key === "file_ids") setValues(old => ({...old, file_ids: [...((old.file_ids ?? []) as string[]), uploaded.id]}));
        else update(key, uploaded.id);
      }
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  function fileField(key: string, required = false) {
    return (
      <label key={key}>
        {key === "other_file_id" && selected === "compare_documents" ? "新版文档" : labels[key]}
        <div className="of-file-picker">
          <select
            required={required}
            value={String(values[key] ?? "")}
            onChange={(e) => update(key, e.target.value || null)}
          >
            <option value="">选择文件</option>
            {files.map((f) => (
              <option value={f.id} key={f.id}>
                {f.name}
              </option>
            ))}
          </select>
          <label className="of-upload-button">
            上传文件
            <input
              type="file"
              accept=".docx,.xlsx,.csv,.txt,.md,.pdf"
              disabled={busy}
              onChange={(e) => {
                if (e.target.files?.[0]) void upload([e.target.files[0]], key);
                e.target.value = "";
              }}
            />
          </label>
        </div>
      </label>
    );
  }
  const ids = (values.file_ids ?? []) as string[];
  const eligibleFiles = files.filter(f => selected === "process_pdf" ? /\.pdf$/i.test(f.name) : selected === "merge_tables" ? /\.(xlsx|csv)$/i.test(f.name) : true);
  const multipleFiles = <fieldset className="of-tool-source"><legend>输入文件（按列表顺序处理，最多 20 份）</legend>
    <div className="of-file-picker"><select aria-label="添加已有文件" value="" disabled={ids.length >= 20 || busy} onChange={e => { if (e.target.value) update("file_ids", [...ids, e.target.value]); }}>
      <option value="">选择文件并添加</option>{eligibleFiles.filter(f => !ids.includes(f.id)).map(f => <option key={f.id} value={f.id}>{f.name}</option>)}
    </select><label className="of-upload-button">上传文件<input type="file" multiple accept={selected === "process_pdf" ? ".pdf" : selected === "merge_tables" ? ".xlsx,.csv" : ".docx,.xlsx,.csv,.txt,.md,.pdf,.zip"} disabled={busy || ids.length >= 20} onChange={e => { const batch = Array.from(e.target.files ?? []); if (batch.length + ids.length > 20) setError("最多选择 20 份文件"); else if (batch.length) void upload(batch, "file_ids"); e.target.value = ""; }} /></label></div>
    <ol className="of-batch-files">{ids.map((id, i) => <li key={id}><span>{files.find(f => f.id === id)?.name ?? "已选择文件"}</span><div>
      <button type="button" aria-label={`上移第 ${i + 1} 份文件`} disabled={i === 0 || busy} onClick={() => { const next = [...ids]; [next[i - 1], next[i]] = [next[i], next[i - 1]]; update("file_ids", next); }}>↑</button>
      <button type="button" aria-label={`下移第 ${i + 1} 份文件`} disabled={i === ids.length - 1 || busy} onClick={() => { const next = [...ids]; [next[i], next[i + 1]] = [next[i + 1], next[i]]; update("file_ids", next); }}>↓</button>
      <button type="button" aria-label={`移除第 ${i + 1} 份文件`} disabled={busy} onClick={() => update("file_ids", ids.filter((_, n) => i !== n))}>移除</button>
    </div></li>)}</ol>
  </fieldset>;
  function text(key: string, placeholder = "", multiline = false) {
    return (
      <label key={key}>
        {labels[key] ?? key}
        {multiline ? (
          <textarea
            rows={key === "content" ? 10 : 4}
            value={String(values[key] ?? "")}
            placeholder={placeholder}
            onChange={(e) => update(key, e.target.value)}
          />
        ) : (
          <input
            value={String(values[key] ?? "")}
            placeholder={placeholder}
            onChange={(e) => update(key, e.target.value)}
          />
        )}
      </label>
    );
  }
  function numeric(key: string, initial: number) {
    return (
      <label key={key}>
        {labels[key]}
        <input
          type="number"
          min={key === "days" ? undefined : 0}
          value={Number(values[key] ?? initial)}
          onChange={(e) => update(key, Number(e.target.value))}
        />
      </label>
    );
  }
  async function run(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const a: Record<string, unknown> = { ...values };
      if (["process_text", "compare_documents", "redact_text"].includes(selected)) {
        for (const prefix of selected === "compare_documents" ? ["", "other_"] : [""]) {
          if (a[prefix + "source_mode"] === "file") delete a[prefix + "content"];
          else delete a[prefix + "file_id"];
          delete a[prefix + "source_mode"];
        }
      }
      for (const key of ["custom_terms", "holidays", "working_dates"])
        if (key in a) a[key] = String(a[key]).split(key === "custom_terms" ? /\n/ : /[,，\s]+/).map(v => v.trim()).filter(Boolean);
      if (selected === "clean_table") a.columns = String(a.columns ?? "").split(/[,，\n]/).map(v => v.trim()).filter(Boolean);
      if (selected === "process_pdf") {
        if (a.rotation) a.rotation = Number(a.rotation);
        if ((a.operation ?? "merge") === "merge") delete a.pages;
      }
      if (selected === "workday_calculate" && a.operation === "add_days") delete a.other_date;
      if (selected === "transform_table") {
        a.operation = a.operation ?? "group";
        a.columns = String(a.columns ?? "")
          .split(/[,，\n]/)
          .map((v) => v.trim())
          .filter(Boolean);
        a.aggregations = a.aggregate_column
          ? { [String(a.aggregate_column)]: a.aggregate_operation ?? "sum" }
          : {};
        a.filters = a.filter_column
          ? [
              {
                column: a.filter_column,
                operator: a.filter_operator ?? "eq",
                value: a.filter_value ?? "",
              },
            ]
          : [];
        for (const key of [
          "aggregate_column",
          "aggregate_operation",
          "filter_column",
          "filter_operator",
          "filter_value",
        ])
          delete a[key];
      }
      if (selected === "validate_table") {
        a.rules = [
          {
            column: String(a.column ?? ""),
            required: a.required ?? true,
            unique: a.unique ?? false,
            type: a.type || null,
            ...(a.minimum !== undefined ? { minimum: a.minimum } : {}),
            ...(a.maximum !== undefined ? { maximum: a.maximum } : {}),
          },
        ];
        for (const key of [
          "column",
          "required",
          "unique",
          "type",
          "minimum",
          "maximum",
        ])
          delete a[key];
      }
      if (selected === "generate_document") {
        a.paragraphs = String(a.paragraph_text ?? "")
          .split("\n")
          .filter(Boolean);
        delete a.paragraph_text;
        if (a.section_text) {
          a.sections = [
            {
              kind: a.section_kind ?? "heading",
              text: a.section_text,
              items: String(a.section_text).split("\n"),
            },
          ];
        }
        delete a.section_text;
        delete a.section_kind;
      }
      if (selected === "generate_spreadsheet") {
        a.headers = String(a.headers ?? "")
          .split(/[,，\t]/)
          .map((v) => v.trim());
        a.rows = String(a.row_text ?? "")
          .split("\n")
          .filter(Boolean)
          .map((line) =>
            line.split(line.includes("\t") ? "\t" : ",").map((v) => v.trim()),
          );
        delete a.row_text;
      }
      if (selected === "date_calculate")
        a.operation = a.operation ?? "difference";
      for (const key of Object.keys(a))
        if ((a[key] === "" && key !== "source_column") || a[key] === undefined) delete a[key];
      const result = await officeApi<Execution>("/tool-executions", {
        tool: selected,
        arguments: a,
      });
      setExecution(result);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="of-tool-workspace" ref={workspace}>
      {catalog ? <ToolCatalog tools={tools} search={search} group={group} busy={busy} onSearch={setSearch} onGroup={setGroup} onOpen={openTool} /> : <>
      <button className="of-tool-back" disabled={busy} onClick={() => setCatalog(true)}>← 返回办公工具</button>
      <div className="of-tool-body">
        {tool && (
          <>
            <div className="of-section-heading">
              <div>
                <span className="of-eyebrow">办公工具</span>
                <h2 tabIndex={-1}>{tool.title}</h2>
                <p>{tool.description}</p>
              </div>
              {isAdmin && (
                <button
                  onClick={async () => {
                    try {
                      await officeApi(
                        "/tools/" + tool.id,
                        { enabled: !tool.enabled },
                        "PATCH",
                      );
                      onRefresh();
                    } catch (e) {
                      setError((e as Error).message);
                    }
                  }}
                >
                  {tool.enabled ? "停用工具" : "启用工具"}
                </button>
              )}
            </div>
            {error && (
              <p className="error" role="alert">
                {error}
              </p>
            )}
            <form className="of-tool-form" onSubmit={run}>
              <ExtraToolFields selected={selected} values={values} update={update} fileField={fileField} text={text} multipleFiles={multipleFiles} />
              {[
                "read_document",
                "read_table",
                "transform_table",
                "validate_table",
              ].includes(selected) && fileField("file_id", true)}
              {["read_table", "transform_table", "validate_table"].includes(
                selected,
              ) && text("sheet")}
              {selected === "read_document" && (
                <div className="of-form-row">
                  {numeric("start", 0)}
                  {numeric("limit", 8000)}
                </div>
              )}
              {selected === "read_table" && (
                <div className="of-form-row">
                  {numeric("offset", 0)}
                  {numeric("limit", 30)}
                </div>
              )}
              {selected === "transform_table" && (
                <>
                  <label>
                    处理方式
                    <select
                      value={String(values.operation ?? "group")}
                      onChange={(e) => update("operation", e.target.value)}
                    >
                      {[
                        ["group", "分组汇总"],
                        ["filter", "筛选记录"],
                        ["sort", "排序"],
                        ["deduplicate", "去重"],
                        ["join", "字段匹配"],
                        ["compare", "差异比较"],
                      ].map(([v, l]) => (
                        <option value={v} key={v}>
                          {l}
                        </option>
                      ))}
                    </select>
                  </label>
                  {text("columns", "例如：人员、部门（多个字段用逗号分隔）")}
                  {(values.operation ?? "group") === "group" && (
                    <div className="of-form-row">
                      <label>
                        统计字段
                        <input
                          required
                          value={String(values.aggregate_column ?? "")}
                          onChange={(e) =>
                            update("aggregate_column", e.target.value)
                          }
                        />
                      </label>
                      <label>
                        统计方法
                        <select
                          value={String(values.aggregate_operation ?? "sum")}
                          onChange={(e) =>
                            update("aggregate_operation", e.target.value)
                          }
                        >
                          {[
                            ["sum", "求和"],
                            ["mean", "平均值"],
                            ["count", "记录数"],
                            ["min", "最小值"],
                            ["max", "最大值"],
                          ].map(([v, l]) => (
                            <option value={v} key={v}>
                              {l}
                            </option>
                          ))}
                        </select>
                      </label>
                    </div>
                  )}
                  {values.operation === "filter" && (
                    <div className="of-form-row">
                      <label>
                        筛选字段
                        <input
                          required
                          value={String(values.filter_column ?? "")}
                          onChange={(e) =>
                            update("filter_column", e.target.value)
                          }
                        />
                      </label>
                      <label>
                        条件
                        <select
                          value={String(values.filter_operator ?? "eq")}
                          onChange={(e) =>
                            update("filter_operator", e.target.value)
                          }
                        >
                          {[
                            ["eq", "等于"],
                            ["ne", "不等于"],
                            ["contains", "包含"],
                            ["gt", "大于"],
                            ["lt", "小于"],
                            ["empty", "为空"],
                          ].map(([v, l]) => (
                            <option value={v} key={v}>
                              {l}
                            </option>
                          ))}
                        </select>
                      </label>
                      <label>
                        比较值
                        <input
                          value={String(values.filter_value ?? "")}
                          onChange={(e) =>
                            update("filter_value", e.target.value)
                          }
                        />
                      </label>
                    </div>
                  )}
                  {values.operation === "sort" && (
                    <label className="of-check">
                      <input
                        type="checkbox"
                        checked={!!values.descending}
                        onChange={(e) => update("descending", e.target.checked)}
                      />
                      降序排列
                    </label>
                  )}
                  {["join", "compare"].includes(String(values.operation)) &&
                    fileField("other_file_id", true)}
                  {text("filename", "处理结果.xlsx")}
                </>
              )}
              {selected === "validate_table" && (
                <>
                  <label>
                    校验字段
                    <input
                      required
                      value={String(values.column ?? "")}
                      onChange={(e) => update("column", e.target.value)}
                    />
                  </label>
                  <div className="of-form-row">
                    <label>
                      类型
                      <select
                        value={String(values.type ?? "")}
                        onChange={(e) => update("type", e.target.value)}
                      >
                        <option value="">不限</option>
                        <option value="text">文本</option>
                        <option value="number">数值</option>
                        <option value="date">日期</option>
                      </select>
                    </label>
                    <label className="of-check">
                      <input
                        type="checkbox"
                        checked={values.required !== false}
                        onChange={(e) => update("required", e.target.checked)}
                      />
                      必填
                    </label>
                    <label className="of-check">
                      <input
                        type="checkbox"
                        checked={!!values.unique}
                        onChange={(e) => update("unique", e.target.checked)}
                      />
                      值唯一
                    </label>
                  </div>
                  <div className="of-form-row">
                    <label>
                      最小值（可选）
                      <input
                        type="number"
                        step="any"
                        value={
                          values.minimum === undefined
                            ? ""
                            : Number(values.minimum)
                        }
                        onChange={(e) =>
                          update(
                            "minimum",
                            e.target.value === ""
                              ? undefined
                              : Number(e.target.value),
                          )
                        }
                      />
                    </label>
                    <label>
                      最大值（可选）
                      <input
                        type="number"
                        step="any"
                        value={
                          values.maximum === undefined
                            ? ""
                            : Number(values.maximum)
                        }
                        onChange={(e) =>
                          update(
                            "maximum",
                            e.target.value === ""
                              ? undefined
                              : Number(e.target.value),
                          )
                        }
                      />
                    </label>
                  </div>
                </>
              )}
              {selected === "calculate" &&
                text("expression", "例如：mean([2, 4, 6]) + 1")}
              {selected === "date_calculate" && (
                <>
                  <label>
                    计算方式
                    <select
                      value={String(values.operation ?? "difference")}
                      onChange={(e) => update("operation", e.target.value)}
                    >
                      <option value="difference">计算相差天数</option>
                      <option value="add_days">增加天数</option>
                    </select>
                  </label>
                  <label>
                    起始日期
                    <input
                      type="date"
                      required
                      value={String(values.date ?? "")}
                      onChange={(e) => update("date", e.target.value)}
                    />
                  </label>
                  {(values.operation ?? "difference") === "difference" ? (
                    <label>
                      结束日期
                      <input
                        type="date"
                        required
                        value={String(values.other_date ?? "")}
                        onChange={(e) => update("other_date", e.target.value)}
                      />
                    </label>
                  ) : (
                    numeric("days", 0)
                  )}
                </>
              )}
              {selected === "generate_document" && (
                <>
                  {text("title", "文档标题")}
                  <label>
                    正文 <small>每行作为一个段落</small>
                    <textarea
                      rows={8}
                      value={String(values.paragraph_text ?? "")}
                      onChange={(e) => update("paragraph_text", e.target.value)}
                    />
                  </label>
                  <details>
                    <summary>章节与模板（可选）</summary>
                    <label>
                      内容类型
                      <select
                        value={String(values.section_kind ?? "heading")}
                        onChange={(e) => update("section_kind", e.target.value)}
                      >
                        <option value="heading">章节标题</option>
                        <option value="list">项目列表</option>
                        <option value="paragraph">补充段落</option>
                      </select>
                    </label>
                    <label>
                      章节内容
                      <textarea
                        rows={3}
                        value={String(values.section_text ?? "")}
                        onChange={(e) => update("section_text", e.target.value)}
                      />
                    </label>
                    {fileField("template_file_id")}
                    {fileField("table_file_id")}
                  </details>
                  {text("filename", "生成文档.docx")}
                </>
              )}
              {selected === "generate_spreadsheet" && (
                <>
                  {text("headers", "人员,工时")}
                  <label>
                    表格内容 <small>可从 Excel 粘贴，每行一条记录</small>
                    <textarea
                      rows={8}
                      value={String(values.row_text ?? "")}
                      onChange={(e) => update("row_text", e.target.value)}
                      placeholder={"张三\t2\n李四\t3"}
                    />
                  </label>
                  {text("filename", "生成表格.xlsx（也支持 .csv）")}
                </>
              )}
              {selected === "export_text" && (
                <>
                  {text("content", "填写要保存的文本", true)}
                  {text("filename", "结果.md（也支持 .txt）")}
                </>
              )}
              <button
                className="of-primary"
                disabled={
                  busy ||
                  !tool.enabled ||
                  (!!execution && !terminal.has(execution.status))
                }
              >
                {busy ? "处理中…" : "开始处理"}
              </button>
            </form>
          </>
        )}
        {execution && (
          <ExecutionCard
            execution={execution}
            onCancel={() =>
              void officeApi<Execution>(
                "/executions/" + execution.id + "/cancel",
                undefined,
                "POST",
              )
                .then(setExecution)
                .catch((e) => setError(e.message))
            }
          />
        )}
      </div>
      </>}
    </div>
  );
}
