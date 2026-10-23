import { useEffect, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { officeApi, type Schema } from "./office-api";

export function LegacyHistory({ onBack, onNew, busy }: {
  onBack: () => void; onNew: () => void; busy: boolean;
}) {
  const [sessions, setSessions] = useState<Schema["ChatSession"][]>([]);
  const [selected, setSelected] = useState("");
  const [messages, setMessages] = useState<Schema["ChatMessage"][]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [reading, setReading] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let alive = true;
    officeApi<Schema["ChatSessions"]>("/chat/sessions").then((data) => {
      if (!alive) return;
      setSessions(data.items);
      const id = location.hash.split("/")[1];
      setSelected(data.items.find((item) => item.session_id === id)?.session_id ?? data.items[0]?.session_id ?? "");
    }).catch((e) => alive && setError(e.message))
      .finally(() => alive && setLoading(false));
    return () => { alive = false; };
  }, []);
  useEffect(() => {
    if (!selected) return;
    let alive = true;
    setMessages([]);
    setError("");
    setReading(true);
    officeApi<Schema["ChatMessages"]>("/chat/sessions/" + encodeURIComponent(selected) + "/messages")
      .then((data) => alive && setMessages(data.items))
      .catch((e) => alive && setError(e.message))
      .finally(() => alive && setReading(false));
    return () => { alive = false; };
  }, [selected]);
  const filtered = sessions.filter((item) => item.title.toLowerCase().includes(query.toLowerCase()));
  const current = sessions.find((item) => item.session_id === selected);
  function select(id: string) {
    setSelected(id);
    history.replaceState(null, "", "#legacy/" + encodeURIComponent(id));
  }
  return <section className="of-legacy-history" aria-label="旧版接待记录">
    <div className="of-section-heading">
      <div><h2>旧版接待记录</h2><p>保留原接待对话供查阅。新对话请使用通用助手，记录统一保存在“我的任务”。</p></div>
      <div className="of-history-actions">
        <button onClick={onBack}>返回我的任务</button>
        <button className="of-primary" disabled={busy} onClick={onNew}>新建通用对话</button>
      </div>
    </div>
    {error && <p className="error" role="alert">{error}</p>}
    <div className="of-legacy-controls">
      <input type="search" aria-label="搜索旧版对话" placeholder="搜索旧版对话" value={query} onChange={(e) => setQuery(e.target.value)} />
      <select className="of-legacy-select" aria-label="选择旧版对话" value={selected} onChange={(e) => select(e.target.value)}>
        <option value="" disabled>选择一条旧版对话</option>
        {filtered.map((item) => <option key={item.session_id} value={item.session_id}>{item.title}</option>)}
      </select>
      <small>{loading ? "正在读取…" : filtered.length + " 条记录 · 只读"}</small>
    </div>
    {!loading && !sessions.length ? <div className="of-empty">暂无旧版接待记录。新的对话和文件任务可在“我的任务”中继续。</div> :
      <div className="of-legacy-layout">
        <aside className="of-legacy-list" aria-label="旧版对话列表">
          {!loading && !filtered.length && <p>没有匹配的旧版对话。</p>}
          {filtered.map((item) => <button key={item.session_id} className={selected === item.session_id ? "active" : ""} aria-current={selected === item.session_id ? "true" : undefined} onClick={() => select(item.session_id)}>
            <strong>{item.title}</strong><small>{new Date(item.created_at).toLocaleString()}</small>
          </button>)}
        </aside>
        <div className="of-legacy-transcript" aria-label="旧版对话内容" key={selected}>
          {current && <h3>{current.title}</h3>}
          {reading ? <p role="status">正在读取对话…</p> : messages.map((message) => <article className={"of-message " + message.role} key={message.message_id}>
            <span className="of-message-avatar" aria-hidden="true">{message.role === "user" ? "我" : "◇"}</span>
            <div><strong>{message.role === "user" ? "你" : message.role === "assistant" ? "原接待助手" : "系统记录"}</strong>
              <div className="of-message-body"><Markdown remarkPlugins={[remarkGfm]} components={{ img: () => null }}>{message.content}</Markdown></div>
              <small className="of-muted">{new Date(message.created_at).toLocaleString()}{message.run_id ? " · 关联运行 " + message.run_id : ""}</small>
            </div>
          </article>)}
          {!loading && !reading && current && !messages.length && <p>这条对话没有消息。</p>}
        </div>
      </div>}
  </section>;
}
