import React, { useState } from "react";

export type PickerItem = {
  id: string;
  title: string;
  description: string;
  group: string;
  locked?: boolean;
  unavailable?: boolean;
};

const pageSize = 8;

export function ResourcePicker({ kind, items, selected, limit, onApply, onCancel }: {
  kind: "tools" | "skills";
  items: PickerItem[];
  selected: string[];
  limit: number;
  onApply: (ids: string[]) => void;
  onCancel: () => void;
}) {
  const [choice, setChoice] = useState(() => new Set(selected));
  const [search, setSearch] = useState(""), [group, setGroup] = useState("全部");
  const [onlySelected, setOnlySelected] = useState(false), [page, setPage] = useState(0);
  const title = kind === "tools" ? "选择工具" : "选择 Skill";
  const query = search.trim().toLowerCase();
  const groups = Array.from(new Set(items.map(item => item.group)));
  const filtered = items.filter(item =>
    (!onlySelected || choice.has(item.id)) && (group === "全部" || item.group === group) &&
    (item.title + item.description).toLowerCase().includes(query),
  );
  const pages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const current = Math.min(page, pages - 1);
  function turn(next: number) {
    setPage(next);
    document.querySelector(".of-content")?.scrollTo({ top: 0 });
  }
  return <section className="of-resource-picker" aria-label={title}>
    <header className="of-picker-heading">
      <div><span className="of-eyebrow">{kind === "tools" ? "添加办公能力" : "组合工作方法"}</span><h2 tabIndex={-1}>{title}</h2></div>
      <div className="of-picker-actions">
        <button type="button" onClick={onCancel}>取消选择</button>
        <button type="button" className="of-primary" onClick={() => onApply(Array.from(choice))}>确认选择（{choice.size} 项）</button>
      </div>
    </header>
    <p className="of-muted">{kind === "tools" ? "按任务需要搭配工具，Skill 必需工具已自动选中。" : "选择要绑定的已发布 Skill，返回后可调整版本。"}最多选择 {limit} 项。</p>
    <div className="of-picker-toolbar">
      <input type="search" aria-label={kind === "tools" ? "搜索搭配工具" : "搜索可选 Skill"} placeholder="搜索名称或用途" value={search} onChange={e => { setSearch(e.target.value); setPage(0); }} />
      <label className="of-picker-selected-filter"><input type="checkbox" checked={onlySelected} onChange={e => { setOnlySelected(e.target.checked); setPage(0); }} />仅看已选（{choice.size}）</label>
    </div>
    <div className="of-tabs of-picker-groups" role="tablist" aria-label="能力分类">
      {["全部", ...groups].map(value => <button type="button" role="tab" key={value} aria-selected={group === value} className={group === value ? "active" : ""} onClick={() => { setGroup(value); setPage(0); }}>{value}</button>)}
    </div>
    <div className="of-picker-grid">
      {filtered.slice(current * pageSize, (current + 1) * pageSize).map(item => {
        const checked = choice.has(item.id);
        const disabled = item.locked || (!checked && (item.unavailable || choice.size >= limit));
        return <label key={item.id} className={"of-picker-card" + (checked ? " is-selected" : "") + (disabled && !checked ? " is-unavailable" : "")}>
          <input type="checkbox" disabled={disabled} checked={checked} onChange={e => setChoice(old => { const next = new Set(old); if (e.target.checked) next.add(item.id); else next.delete(item.id); return next; })} />
          <span><strong>{item.title}</strong><small>{item.description}</small><span className="of-picker-card-meta">{item.group}{item.locked ? " · Skill 必需" : item.unavailable ? " · 暂不可用" : ""}</span></span>
        </label>;
      })}
      {!filtered.length && <p className="of-empty" role="status">没有匹配的{kind === "tools" ? "工具" : " Skill"}，请更换搜索词或分类。</p>}
    </div>
    <footer className="of-picker-pagination" aria-label="选择列表分页">
      <span>共 {filtered.length} 项 · 第 {current + 1} / {pages} 页</span>
      <div><button type="button" disabled={current === 0} onClick={() => turn(current - 1)}>上一页</button><button type="button" disabled={current + 1 >= pages} onClick={() => turn(current + 1)}>下一页</button></div>
    </footer>
  </section>;
}
