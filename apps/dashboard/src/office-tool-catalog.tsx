import React from "react";
import type { Tool } from "./office-api";
import { toolGroups } from "./office-tools-extra";

export const toolSummaries: Record<string, string> = {
  read_document: "读取 Word、文本和文本型 PDF，快速查看材料内容。",
  read_table: "查看 Excel、CSV 的工作表、字段和数据记录。",
  transform_table: "筛选、排序、分组汇总、去重，或匹配比较两张表。",
  validate_table: "检查必填项、数据类型、数值范围和重复记录。",
  calculate: "完成日常算术、求和、平均值等数值与统计计算。",
  date_calculate: "计算日期间隔，或按指定天数推算日期。",
  generate_document: "将正文、章节、列表和附表整理为可下载的 Word。",
  generate_spreadsheet: "填写或粘贴表格数据，生成 Excel 或 CSV 文件。",
  export_text: "将文字内容保存为 TXT 或 Markdown 文件。",
  process_pdf: "合并多份 PDF，提取指定页面，或调整页面方向。",
  merge_tables: "按字段名合并多份 Excel、CSV，可增加来源文件列。",
  clean_table: "清理文本空白、统一全角字符，转换 Excel 与 CSV。",
  process_text: "清理多余空白、按行去重，统计字符、汉字与单词。",
  compare_documents: "比较两版文稿，查看新增、删除内容并导出差异。",
  redact_text: "遮盖手机号、身份证号、邮箱及指定词语，分享前请复核。",
  workday_calculate: "推算工作日和截止日期，支持自行填写放假与补班日期。",
  bundle_files: "将多份办公材料整理为 ZIP，同名文件自动编号。",
};

function ToolIcon({ group }: { group: string }) {
  const shape = group === "表格与数据"
    ? <><rect x="4" y="4" width="16" height="16" rx="2" /><path d="M4 10h16M4 15h16M10 4v16" /></>
    : group === "文本与隐私"
      ? <><path d="M5 6h14M5 10h14M5 14h9M5 18h9" /><path d="m17 16 2 2 3-4" /></>
      : group === "计算与文件"
        ? <><rect x="5" y="3" width="14" height="18" rx="2" /><path d="M8 7h8M8 12h2m4 0h2M8 16h2m4 0h2" /></>
        : <><path d="M14 3H6a1 1 0 0 0-1 1v16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1V8Z" /><path d="M14 3v5h5M8 12h8M8 16h6" /></>;
  return <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{shape}</svg>;
}

export function ToolCatalog({ tools, search, group, busy, onSearch, onGroup, onOpen }: {
  tools: Tool[];
  search: string;
  group: string;
  busy: boolean;
  onSearch: (value: string) => void;
  onGroup: (value: string) => void;
  onOpen: (id: string) => void;
}) {
  const groups = Array.from(new Set(tools.map(t => toolGroups[t.id] ?? "其他工具")));
  const query = search.trim().toLowerCase();
  const filtered = tools.filter(t =>
    (group === "全部" || (toolGroups[t.id] ?? "其他工具") === group) &&
    (t.title + t.description + (toolSummaries[t.id] ?? "")).toLowerCase().includes(query),
  );
  return <>
    <section className="of-welcome of-tool-welcome">
      <div>
        <span className="of-eyebrow">日常办公，随手处理</span>
        <h2>为手头的工作，选一个工具。</h2>
        <p>整理文档、处理表格、计算日期或打包材料，选择工具即可开始。</p>
      </div>
      <span className="of-badge">{tools.length} 项办公工具</span>
    </section>
    <div className="of-list-toolbar of-tool-toolbar">
      <div className="of-tabs" role="tablist" aria-label="工具分类">
        {["全部", ...groups].map(value => <button key={value} role="tab" aria-selected={group === value} className={group === value ? "active" : ""} onClick={() => onGroup(value)}>{value}</button>)}
      </div>
      <input type="search" aria-label="搜索办公工具" placeholder="搜索工具名称或用途" value={search} onChange={e => onSearch(e.target.value)} />
    </div>
    <div className="of-cards of-tool-cards">
      {filtered.map(t => <article className="of-resource-card of-tool-card" key={t.id} data-tool-id={t.id} aria-labelledby={"tool-title-" + t.id}>
        <div className="of-row">
          <span className="of-card-symbol"><ToolIcon group={toolGroups[t.id]} /></span>
          <span className="of-badge">{toolGroups[t.id] ?? "其他工具"}</span>
        </div>
        <h3 id={"tool-title-" + t.id}>{t.title}</h3>
        <p>{toolSummaries[t.id] ?? t.description}</p>
        <div className="of-card-actions">
          <span className={"of-tool-status" + (t.enabled ? " enabled" : "")}>{t.enabled ? "可直接使用" : "管理员已停用"}</span>
          <button className={t.enabled ? "of-primary" : ""} disabled={busy} onClick={() => onOpen(t.id)}>{t.enabled ? "开始使用 ↗" : "查看工具 ↗"}</button>
        </div>
      </article>)}
      {!filtered.length && <div className="of-empty" role="status">没有匹配的工具，请更换分类或搜索词。</div>}
    </div>
  </>;
}
