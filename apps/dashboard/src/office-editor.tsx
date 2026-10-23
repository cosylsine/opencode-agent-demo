import React, { useLayoutEffect, useRef, useState } from "react";
import { AutoTextarea } from "./auto-textarea";
import { ResourcePicker, type PickerItem } from "./resource-picker";
import { toolGroups } from "./office-tools-extra";
import { toolSummaries } from "./office-tool-catalog";
import {
  officeApi,
  type Agent,
  type Resource,
  type Skill,
  type Tool,
} from "./office-api";

const blankSkill: Skill = {
  title: "",
  description: "",
  applicability: "",
  inputs: "",
  instructions: "",
  exceptions: "",
  output_format: "",
  tools: [],
};
const blankAgent: Agent = {
  title: "",
  description: "",
  instructions: "",
  greeting: "",
  examples: [],
  skills: [],
  tools: [],
};

export function ResourceEditor({
  kind,
  resource,
  skills,
  tools,
  initialSkill,
  onSaved,
  onClose,
}: {
  kind: "skill" | "agent";
  resource?: Resource;
  skills: Resource[];
  tools: Tool[];
  initialSkill?: Resource;
  onSaved: (resource: Resource) => void;
  onClose: () => void;
}) {
  const [value, setValue] = useState<Skill | Agent>(() => {
    const initial = resource?.content ?? (kind === "skill" ? blankSkill : {
      ...blankAgent,
      title: initialSkill ? initialSkill.content.title + "助手" : "",
      description: initialSkill?.content.description ?? "",
      skills: initialSkill ? [{ id: initialSkill.id, version: initialSkill.published_version }] : [],
    });
    if (kind === "skill") return initial;
    const inherited = new Set((initial as Agent).skills.flatMap(ref => skills.find(skill => skill.id === ref.id)?.content.tools ?? []));
    return { ...initial, tools: initial.tools.filter(id => !inherited.has(id)) };
  });
  const [markdown, setMarkdown] = useState(false),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [visibility, setVisibility] = useState<"private" | "public">(resource?.visibility ?? "private");
  const [picker, setPicker] = useState<"tools" | "skills" | null>(null);
  const root = useRef<HTMLDivElement>(null), editorScroll = useRef(0), lastPicker = useRef("");
  const [advancedOpen, setAdvancedOpen] = useState(() => kind === "skill"
    ? Boolean((resource?.content as Skill | undefined)?.applicability || (resource?.content as Skill | undefined)?.inputs || (resource?.content as Skill | undefined)?.exceptions || (resource?.content as Skill | undefined)?.output_format)
    : Boolean((resource?.content as Agent | undefined)?.greeting || (resource?.content as Agent | undefined)?.examples.length));
  useLayoutEffect(() => {
    const page = root.current?.closest<HTMLElement>(".of-content");
    const heading = root.current?.querySelector<HTMLElement>(".of-editor-heading, .of-picker-heading");
    if (!page || !heading) return;
    // Reserve the actual responsive heading height when a field is focused
    // or scrolled into view, including after switching to the picker.
    const fit = () => page.style.setProperty("--of-editor-heading-height", Math.ceil(heading.getBoundingClientRect().height) + "px");
    fit();
    const observer = new ResizeObserver(fit);
    observer.observe(heading);
    return () => { observer.disconnect(); page.style.removeProperty("--of-editor-heading-height"); };
  }, [picker]);
  useLayoutEffect(() => {
    if (!lastPicker.current) return;
    const page = root.current?.closest(".of-content");
    if (page) page.scrollTop = picker ? 0 : editorScroll.current;
    const focus = picker
      ? root.current?.querySelector<HTMLHeadingElement>(".of-resource-picker h2")
      : root.current?.querySelector<HTMLButtonElement>(`[data-picker-trigger="${lastPicker.current}"]`);
    focus?.focus({ preventScroll: true });
  }, [picker]);
  function openPicker(kind: "tools" | "skills") {
    editorScroll.current = root.current?.closest(".of-content")?.scrollTop ?? 0;
    lastPicker.current = kind;
    setPicker(kind);
  }
  const availableSkills = skills.filter(
    (s) => !s.archived && s.published_version > 0,
  );
  const references = kind === "agent" ? (value as Agent).skills : [];
  const required = new Set(
    references.flatMap(
      (ref) =>
        (skills.find((s) => s.id === ref.id)?.content as Skill | undefined)
          ?.tools ?? [],
    ),
  );
  const selected = new Set([...value.tools, ...required]);
  const toolItems: PickerItem[] = tools.map(tool => ({
    id: tool.id, title: tool.title, description: toolSummaries[tool.id] ?? tool.description,
    group: toolGroups[tool.id] ?? "其他工具", locked: required.has(tool.id), unavailable: !tool.enabled,
  }));
  const skillItems: PickerItem[] = availableSkills.map(skill => ({
    id: skill.id, title: skill.content.title, description: skill.content.description,
    group: skill.builtin ? "平台内置" : skill.visibility === "public" ? "公共 Skill" : "我的 Skill",
  }));
  for (const ref of references) if (!skillItems.some(item => item.id === ref.id)) skillItems.push({
    id: ref.id, title: skills.find(skill => skill.id === ref.id)?.content.title ?? "不可用的 Skill",
    description: "已归档或不再共享，请移除后选择可用 Skill。", group: "不可用", unavailable: true,
  });
  function applySkills(ids: string[]) {
    update("skills", ids.map(id => references.find(ref => ref.id === id) ?? { id, version: skills.find(skill => skill.id === id)!.published_version }));
    setPicker(null);
  }
  function removeSkill(id: string) {
    applySkills(references.filter(ref => ref.id !== id).map(ref => ref.id));
  }
  const privateDependencies = references.some(ref => skills.find(s => s.id === ref.id)?.visibility !== "public");
  const update = (key: string, v: unknown) =>
    setValue((old) => ({ ...old, [key]: v }));
  async function save(publish: boolean) {
    setBusy(true);
    setError("");
    try {
      const body = { ...value, tools: Array.from(selected), visibility, publish };
      const result = await officeApi<Resource>(
        "/" +
          (kind === "skill" ? "skills" : "agents") +
          (resource ? "/" + resource.id : ""),
        body,
        resource ? "PUT" : "POST",
      );
      onSaved(result);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="of-editor of-editor-single" ref={root}>
      {picker ? <ResourcePicker
        key={picker} kind={picker} items={picker === "tools" ? toolItems : skillItems}
        selected={picker === "tools" ? Array.from(selected) : references.map(ref => ref.id)}
        limit={picker === "tools" ? 20 : 8}
        onCancel={() => setPicker(null)}
        onApply={ids => { if (picker === "skills") applySkills(ids); else { update("tools", [...value.tools.filter(id => required.has(id)), ...ids.filter(id => !required.has(id))]); setPicker(null); } }}
      /> : <>

      <div className="of-section-heading of-editor-heading">
        <div>
          <span className="of-eyebrow">
            {kind === "skill" ? "沉淀工作方法" : "组装业务助手"}
          </span>
          <h2>
            {resource ? "编辑" : "创建"}
            {kind === "skill" ? " Skill" : "智能体"}
          </h2>
        </div>
        <div className="of-editor-top-actions">
          <button type="button" disabled={busy} onClick={onClose}>返回列表</button>
          <button type="submit" form="office-resource-form" disabled={busy}>保存草稿</button>
          <button className="of-primary" type="button" disabled={busy || !value.title || !value.description || (kind === "skill" ? !value.instructions : !references.length)} onClick={() => void save(true)}>{busy ? "保存中…" : "保存并发布"}</button>
        </div>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void save(false);
        }}
        id="office-resource-form" className="of-editor-form"
      >
        <div className="of-form-panel">
          <label>
            名称
            <input
              required
              maxLength={80}
              value={value.title}
              onChange={(e) => update("title", e.target.value)}
              placeholder={
                kind === "skill" ? "例如：部门周报整理" : "例如：部门周报助手"
              }
            />
          </label>
          <label>
            可见范围
            <select aria-label="可见范围" value={visibility} onChange={e => setVisibility(e.target.value as "private" | "public")}>
              <option value="private">私有 · 仅自己可见和使用</option>
              <option value="public">公共 · 发布后所有成员可见和使用</option>
            </select>
            <small>{resource?.published_version ? "修改可见范围后保存立即生效，不需要单独发布；任务、附件和产物仍按账号隔离。" : "公共资源也需要发布后才对其他成员开放，未发布的草稿不会公开。"}</small>
          </label>
          {visibility === "public" && privateDependencies && <p className="of-callout">公共智能体依赖的 Skill 也必须是公共资源。请先公开所选 Skill，或将此智能体设为私有。</p>}
          <label>
            用途
            <AutoTextarea aria-label="用途"
              required
              rows={2}
              maxLength={1000}
              value={value.description}
              onChange={(e) => update("description", e.target.value)}
              placeholder="说明解决什么问题，以及适合什么任务"
            />
          </label>
          {kind === "skill" ? (
            <>
              <div className="of-field-heading">
                <strong>执行步骤</strong>
                <button
                  type="button"
                  className="of-link"
                  onClick={() => setMarkdown((v) => !v)}
                >
                  {markdown ? "表单模式" : "Markdown 模式"}
                </button>
              </div>
              <label>
                <span className="sr-only">执行步骤</span>
                <AutoTextarea aria-label="执行步骤"
                  className={markdown ? "of-markdown-editor" : ""}
                  required
                  rows={markdown ? 15 : 8}
                  maxLength={12000}
                  value={value.instructions}
                  onChange={(e) => update("instructions", e.target.value)}
                  placeholder={
                    "1. 读取用户上传的工作记录\n2. 检查必填字段，缺少信息时询问用户\n3. 调用统计工具汇总数据\n4. 生成周报文件"
                  }
                />
              </label>
              <details className="of-editor-details" open={advancedOpen} onToggle={e => setAdvancedOpen(e.currentTarget.open)}><summary>适用条件、输入和输出要求（可选）</summary>
              <label>
                适用条件
                <AutoTextarea aria-label="适用条件"
                  rows={2}
                  maxLength={2000}
                  value={(value as Skill).applicability}
                  onChange={(e) => update("applicability", e.target.value)}
                  placeholder="什么情况下使用这个技能"
                />
              </label>
              <label>
                输入要求
                <AutoTextarea aria-label="输入要求"
                  rows={2}
                  maxLength={2000}
                  value={(value as Skill).inputs}
                  onChange={(e) => update("inputs", e.target.value)}
                  placeholder="需要哪些附件、字段和补充信息"
                />
              </label>
              <label>
                异常处理
                <AutoTextarea aria-label="异常处理"
                  rows={2}
                  maxLength={2000}
                  value={(value as Skill).exceptions}
                  onChange={(e) => update("exceptions", e.target.value)}
                  placeholder="材料不完整、格式不符或工具失败时如何处理"
                />
              </label>
              <label>
                输出要求
                <AutoTextarea aria-label="输出要求"
                  rows={2}
                  maxLength={2000}
                  value={(value as Skill).output_format}
                  onChange={(e) => update("output_format", e.target.value)}
                  placeholder="需要生成什么文件，包含哪些章节或字段"
                />
              </label>
              </details>
            </>
          ) : (
            <>
              <label>
                工作要求
                <AutoTextarea aria-label="工作要求"
                  rows={5}
                  maxLength={8000}
                  value={value.instructions}
                  onChange={(e) => update("instructions", e.target.value)}
                  placeholder="角色、语气，以及处理任务时需要遵守的要求"
                />
              </label>
              <details className="of-editor-details" open={advancedOpen} onToggle={e => setAdvancedOpen(e.currentTarget.open)}><summary>开场白和示例问题（可选）</summary>
              <label>
                开场白
                <AutoTextarea aria-label="开场白"
                  rows={2}
                  maxLength={1000}
                  value={(value as Agent).greeting}
                  onChange={(e) => update("greeting", e.target.value)}
                  placeholder="请上传工作记录，并告诉我周报的统计周期。"
                />
              </label>
              <label>
                示例问题 <small>每行一条，最多 6 条</small>
                <AutoTextarea aria-label="示例问题"
                  rows={3}
                  value={(value as Agent).examples.join("\n")}
                  onChange={(e) =>
                    update("examples", e.target.value.split("\n").slice(0, 6))
                  }
                  placeholder={
                    "帮我把工作记录整理成周报\n检查这份材料有没有遗漏"
                  }
                />
              </label>
              </details>

            </>
          )}
        </div>
        {kind === "agent" && <section className="of-form-panel of-config-section" aria-label="绑定的 Skill">
          <div className="of-row"><div><h3>绑定的 Skill <small>{references.length} / 8</small></h3><p>组合已发布的工作方法，所需工具会自动带入。</p></div><button type="button" disabled={busy} data-picker-trigger="skills" onClick={() => openPicker("skills")}>选择 Skill</button></div>
          <div className="of-config-skills">
            {references.map(ref => { const skill = skills.find(skill => skill.id === ref.id); return <div className="of-config-skill" key={ref.id}>
              <div><strong>{skill?.content.title ?? "不可用的 Skill"}</strong><small>{skill?.archived ? "已归档" : skill?.content.description ?? "资源已删除或不再共享"}</small></div>
              <label className="of-version-choice">版本<select aria-label={"Skill 版本：" + (skill?.content.title ?? "不可用的 Skill")} value={ref.version} onChange={e => update("skills", references.map(item => item.id === ref.id ? { ...item, version: Number(e.target.value) } : item))}>
                {Array.from({ length: Math.max(ref.version, skill?.published_version ?? 0) }, (_, i) => i + 1).map(version => <option value={version} key={version}>v{version}</option>)}
              </select></label>
              <button type="button" className="of-link" aria-label={"移除 Skill：" + (skill?.content.title ?? "不可用的 Skill")} onClick={() => removeSkill(ref.id)}>移除</button>
            </div>; })}
            {!references.length && <p className="of-muted">尚未绑定 Skill，选择至少一项后即可发布智能体。</p>}
          </div>
        </section>}
        <section className="of-form-panel of-config-section" aria-label="已选工具">
          <div className="of-row"><div><h3>{kind === "skill" ? "依赖工具" : "搭配工具"} <small>已选 {selected.size} 项</small></h3><p>{kind === "skill" ? "添加执行这些步骤需要的工具。" : "已自动带入 Skill 必需工具，可按需添加其他能力。"}</p></div><button type="button" disabled={busy} data-picker-trigger="tools" onClick={() => openPicker("tools")}>选择工具</button></div>
          <div className="of-config-tools">
            {Array.from(selected).map(id => <span className="of-config-tool" key={id}>
              {tools.find(tool => tool.id === id)?.title ?? id}
              {required.has(id) ? <small>Skill 必需</small> : <button type="button" aria-label={"移除工具：" + (tools.find(tool => tool.id === id)?.title ?? id)} onClick={() => update("tools", value.tools.filter(tool => tool !== id))}>×</button>}
            </span>)}
            {!selected.size && <p className="of-muted">尚未选择工具，可按工作需要添加。</p>}
          </div>
        </section>

      </form>
      </>}
    </div>
  );
}
