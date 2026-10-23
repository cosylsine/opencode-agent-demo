import React, { useCallback, useEffect, useRef, useState } from "react";
import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  officeApi,
  FileCards,
  terminal,
  type Agent,
  type Conversation,
  type Execution,
  type OfficeFile,
  type OfficeMessage,
  type Resource,
  type Schema,
  type Skill,
  type Tool,
  type User,
} from "./office-api";
import { ResourceEditor } from "./office-editor";
import { ExecutionCard, ToolWorkspace } from "./office-tools-ui";
import "./office.css";
import { useChatScroll } from "./chat-scroll";
import openCodeLogo from "./assets/opencode-logo.svg";
import { LegacyHistory } from "./legacy-history";
import { STWorkspace } from "./st-code";

type View =
  | "agents"
  | "skills"
  | "tools"
  | "work"
  | "account"
  | "admin"
  | "legacy"
  | "console"
  | "tuning"
  | "st-code";
const navigation: { id: View; label: string; mark: string }[] = [
  { id: "agents", label: "智能体中心", mark: "◈" },
  { id: "work", label: "我的任务", mark: "▤" },
  { id: "skills", label: "Skill 中心", mark: "◇" },
  { id: "tools", label: "办公工具", mark: "⌘" },
  { id: "console", label: "Monitor 诊断", mark: "⌁" },
  { id: "tuning", label: "B 回路仿真调优", mark: "↗" },
  { id: "st-code", label: "ST 代码优化", mark: "{ }" },
];
const generalAssistantId = "builtin-agent-general-assistant";
const sidebarMinWidth = 220, sidebarMaxWidth = 420, sidebarDefaultWidth = 228;
const clampSidebarWidth = (width: number) =>
  Math.max(sidebarMinWidth, Math.min(sidebarMaxWidth, width));
function sidebarPreference(key: string) {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function AuthForm({
  initialized,
  onAuthenticated,
}: {
  initialized: boolean;
  onAuthenticated: () => void;
}) {
  const [register, setRegister] = useState(false),
    [username, setUsername] = useState(""),
    [name, setName] = useState(""),
    [password, setPassword] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      if (register)
        await officeApi("/auth/register", {
          username,
          password,
          display_name: name,
        });
      await officeApi("/auth/login", { username, password });
      onAuthenticated();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="of-auth">
      <aside className="of-auth-story">
        <div className="of-brand">
          <span>◈</span>
          <strong>部门智能体工作台</strong>
        </div>
        <div>
          <span className="of-eyebrow">让工作经验成为可复用的能力</span>
          <h1>
            你的工作方法，
            <br />
            你的智能助手。
          </h1>
          <p>
            选择一个助手，带上工作材料，
            <br />
            从一条指令开始，完成手头的任务。
          </p>
          <div className="of-auth-flow">
            <span>工作材料</span>
            <b>→</b>
            <span>专属技能</span>
            <b>→</b>
            <span>成果文件</span>
          </div>
        </div>
        <small>部门内使用 · 个人任务与文件独立保存</small>
      </aside>
      <main className="of-auth-main">
        <form onSubmit={submit}>
          <div className="of-auth-heading">
            <span className="of-eyebrow">欢迎使用</span>
            <img className="of-opencode-logo" src={openCodeLogo} width="117" height="21" alt="OpenCode" />
          </div>
          <h2>{register ? "创建你的账号" : "登录工作台"}</h2>
          <p>
            {register
              ? "注册后即可使用公共助手和办公工具。"
              : "继续处理任务，或创建自己的智能助手。"}
          </p>
          {!initialized && (
            <div className="of-callout">
              平台尚未初始化。请联系管理员先完成初始账号设置。
              <details>
                <summary>管理员设置说明</summary>
                <p>
                  在服务器停服后运行 <code>sudo bash deploy.sh init-admin</code>
                  ，按提示创建管理员，再启动平台。
                </p>
              </details>
            </div>
          )}
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
          <label>
            用户名
            <input
              required
              autoComplete="username"
              minLength={3}
              maxLength={40}
              pattern="[a-zA-Z0-9][a-zA-Z0-9_.\-]{2,39}"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="字母、数字、点、横线或下划线"
            />
          </label>
          {register && (
            <label>
              显示名称
              <input
                required
                maxLength={80}
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="你的姓名或常用称呼"
              />
            </label>
          )}
          <label>
            密码
            <input
              required
              type="password"
              autoComplete={register ? "new-password" : "current-password"}
              minLength={10}
              maxLength={128}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="至少 10 个字符"
            />
          </label>
          <button className="of-primary" disabled={busy || !initialized}>
            {busy ? "请稍候…" : register ? "注册并登录" : "登录"}
          </button>
          <p className="of-auth-switch">
            {register ? "已经有账号？" : "还没有账号？"}{" "}
            <button
              type="button"
              className="of-link"
              disabled={!initialized}
              onClick={() => {
                setRegister((v) => !v);
                setError("");
              }}
            >
              {register ? "返回登录" : "立即注册"}
            </button>
          </p>
        </form>
      </main>
    </div>
  );
}

function AccountPanel({
  user,
  onChanged,
}: {
  user: User;
  onChanged: () => void;
}) {
  const [current, setCurrent] = useState(""),
    [next, setNext] = useState(""),
    [confirm, setConfirm] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  return (
    <section className="of-account">
      <span className="of-eyebrow">账号设置</span>
      <h2>{user.display_name}</h2>
      <p>
        {user.username} · {user.is_admin ? "管理员" : "部门成员"}
      </p>
      {user.must_change_password && (
        <p className="of-callout">
          管理员已重置你的密码。请先设置新密码，再继续使用。
        </p>
      )}
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setError("");
          if (next !== confirm) {
            setError("两次新密码不一致");
            return;
          }
          setBusy(true);
          try {
            await officeApi("/auth/password", {
              current_password: current,
              new_password: next,
            });
            onChanged();
          } catch (e) {
            setError((e as Error).message);
          } finally {
            setBusy(false);
          }
        }}
      >
        <h3>修改密码</h3>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        <label>
          当前密码
          <input
            type="password"
            required
            autoComplete="current-password"
            value={current}
            onChange={(e) => setCurrent(e.target.value)}
          />
        </label>
        <label>
          新密码
          <input
            type="password"
            required
            autoComplete="new-password"
            minLength={10}
            maxLength={128}
            value={next}
            onChange={(e) => setNext(e.target.value)}
          />
        </label>
        <label>
          确认新密码
          <input
            type="password"
            required
            autoComplete="new-password"
            minLength={10}
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
        </label>
        <button className="of-primary" disabled={busy}>
          修改并重新登录
        </button>
      </form>
    </section>
  );
}

function AdminPanel() {
  const [users, setUsers] = useState<User[]>([]),
    [error, setError] = useState(""),
    [target, setTarget] = useState<User | null>(null),
    [password, setPassword] = useState("");
  const refresh = () =>
    officeApi<User[]>("/admin/users")
      .then(setUsers)
      .catch((e) => setError(e.message));
  useEffect(() => {
    void refresh();
  }, []);
  async function change(user: User, body: unknown) {
    try {
      await officeApi("/admin/users/" + user.id, body, "PATCH");
      setTarget(null);
      setPassword("");
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  return (
    <section>
      <span className="of-eyebrow">平台管理</span>
      <h2>账号管理</h2>
      <p>用户自行注册即可使用；停用或重置密码会立即撤销原登录状态。</p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="of-table-scroll">
        <table>
          <thead>
            <tr>
              <th>成员</th>
              <th>用户名</th>
              <th>状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {users.map((user) => (
              <tr key={user.id}>
                <td>
                  {user.display_name}
                  {user.is_admin && " · 管理员"}
                </td>
                <td>{user.username}</td>
                <td>
                  {user.disabled
                    ? "已停用"
                    : user.must_change_password
                      ? "待修改密码"
                      : "正常"}
                </td>
                <td>
                  {!user.is_admin && (
                    <button
                      onClick={() =>
                        void change(user, { disabled: !user.disabled })
                      }
                    >
                      {user.disabled ? "启用" : "停用"}
                    </button>
                  )}
                  <button onClick={() => setTarget(user)}>重置密码</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {target && (
        <form
          className="of-form-panel of-reset"
          onSubmit={(e) => {
            e.preventDefault();
            void change(target, { password });
          }}
        >
          <h3>重置 {target.display_name} 的密码</h3>
          <p>请通过部门认可的方式告知本人；登录后必须修改。</p>
          <label>
            临时密码
            <input
              type="password"
              required
              minLength={10}
              maxLength={128}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          <div className="of-actions">
            <button type="button" onClick={() => setTarget(null)}>
              取消
            </button>
            <button className="of-primary">确认重置</button>
          </div>
        </form>
      )}
    </section>
  );
}

export function OfficeApp({
  renderLegacy,
}: {
  renderLegacy: (
    view: "console" | "tuning",
    onNavigate: (view: string) => void,
  ) => React.ReactNode;
}) {
  const [sidebarWidth, setSidebarWidth] = useState(() =>
      clampSidebarWidth(Number(sidebarPreference("office-sidebar-width")) || sidebarDefaultWidth),
    ),
    [sidebarCollapsed, setSidebarCollapsed] = useState(() =>
      sidebarPreference("office-sidebar-collapsed") === "true",
    ),
    [sidebarResizing, setSidebarResizing] = useState(false);
  const sidebarDrag = useRef<{ pointerId: number; x: number; width: number } | null>(null);
  useEffect(() => {
    if (sidebarResizing) return;
    try {
      localStorage.setItem("office-sidebar-width", String(sidebarWidth));
      localStorage.setItem("office-sidebar-collapsed", String(sidebarCollapsed));
    } catch {
      // Layout controls remain usable when browser storage is unavailable.
    }
  }, [sidebarWidth, sidebarCollapsed, sidebarResizing]);
  const [auth, setAuth] = useState<Schema["AuthInfo"] | null>(null),
    [view, setView] = useState<View>(() => {
      const v = location.hash.slice(1).split("/")[0];
      if (v === "chat") return "legacy";
      return [
        "agents",
        "skills",
        "tools",
        "work",
        "account",
        "admin",
        "legacy",
        "console",
        "tuning",
        "st-code",
      ].includes(v)
        ? (v as View)
        : "agents";
    });
  const [agents, setAgents] = useState<Resource[]>([]),
    [skills, setSkills] = useState<Resource[]>([]),
    [tools, setTools] = useState<Tool[]>([]),
    [conversations, setConversations] = useState<Conversation[]>([]);
  const [files, setFiles] = useState<OfficeFile[]>([]),
    [error, setError] = useState(""),
    [search, setSearch] = useState(""),
    [filter, setFilter] = useState("all");
  const [editing, setEditing] = useState<{
      kind: "skill" | "agent";
      resource?: Resource;
      initialSkill?: Resource;
    } | null>(null),
    [detail, setDetail] = useState<Resource | null>(null);
  const [resourceToDelete, setResourceToDelete] = useState<Resource | null>(null);
  const [resourceDeleteError, setResourceDeleteError] = useState(""), [resourceDeleting, setResourceDeleting] = useState(false);
  const resourceModal = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (resourceToDelete && resourceModal.current && !resourceModal.current.open) resourceModal.current.showModal();
  }, [resourceToDelete]);
  const [conversation, setConversation] = useState<Conversation | null>(null),
    [messages, setMessages] = useState<OfficeMessage[]>([]),
    [execution, setExecution] = useState<Execution | null>(null);
  const [draft, setDraft] = useState(""),
    [attachments, setAttachments] = useState<OfficeFile[]>([]),
    [busy, setBusy] = useState(false),
    [confirmDelete, setConfirmDelete] = useState<Conversation | null>(null),
    [rename, setRename] = useState<Conversation | null>(null),
    [renameText, setRenameText] = useState("");
  const [modelReady, setModelReady] = useState<boolean | null>(null);
  const composer = useRef<HTMLTextAreaElement>(null),
    modal = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if ((rename || confirmDelete) && modal.current && !modal.current.open)
      modal.current.showModal();
  }, [rename, confirmDelete]);
  const chatScroll = useChatScroll(
    conversation?.id ?? null,
    view === "work",
    `${messages.length}:${execution?.events.length}`,
  );
  const mergeFiles = useCallback(
    (incoming: OfficeFile[]) =>
      setFiles((old) => [
        ...old,
        ...incoming.filter((f) => !old.some((o) => o.id === f.id)),
      ]),
    [],
  );
  const refreshAuth = useCallback(
    () =>
      officeApi<Schema["AuthInfo"]>("/auth/me")
        .then((value) => {
          setAuth(value);
          if (value.user?.must_change_password) setView("account");
        })
        .catch((e) => setError(e.message)),
    [],
  );
  const refresh = useCallback(async () => {
    const [a, s, t, c] = await Promise.all([
      officeApi<Schema["ResourceList"]>("/agents"),
      officeApi<Schema["ResourceList"]>("/skills"),
      officeApi<Schema["ToolList"]>("/tools"),
      officeApi<Schema["ConversationList"]>("/conversations"),
    ]);
    setAgents(a.items.sort((a, b) => Number(b.id === generalAssistantId) - Number(a.id === generalAssistantId)));
    setSkills(s.items);
    setTools(t.items);
    setConversations(c.items);
  }, []);
  useEffect(() => {
    void refreshAuth();
    const expired = () => {
      setAuth((old) => (old ? { ...old, user: null } : old));
      setConversation(null);
      setMessages([]);
      setFiles([]);
      setExecution(null);
    };
    window.addEventListener("office-auth-expired", expired);
    return () => window.removeEventListener("office-auth-expired", expired);
  }, [refreshAuth]);
  useEffect(() => {
    if (auth?.user && !auth.user.must_change_password) {
      void refresh()
        .then(async () => {
          const [route, id] = location.hash.slice(1).split("/");
          if (route === "work" && id) {
            const list =
              await officeApi<Schema["ConversationList"]>("/conversations");
            const item = list.items.find((c) => c.id === id);
            if (item) await openTask(item);
          }
        })
        .catch((e) => setError(e.message));
      void officeApi<Schema["AvailableModels"]>("/models")
        .then((r) => setModelReady(r.items.some((m) => m.ready)))
        .catch(() => setModelReady(false));
    }
  }, [auth?.user?.id, auth?.user?.must_change_password, refresh]);
  useEffect(() => {
    if (!execution || terminal.has(execution.status)) return;
    let alive = true;
    const timer = window.setInterval(() => {
      officeApi<Execution>("/executions/" + execution.id)
        .then(async (result) => {
          const data = result.conversation_id
            ? await officeApi<Schema["OfficeMessages"]>(
                "/conversations/" + result.conversation_id + "/messages",
              )
            : null;
          if (!alive) return;
          if (data) setMessages(data.items);
          mergeFiles(result.files);
          setExecution(result);
        })
        .catch((e) => alive && setError(e.message));
    }, 1000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, [execution?.id, execution?.status, mergeFiles]);
  async function navigate(next: View) {
    history.replaceState(null, "", "#" + next);
    if (next === "work") {
      setConversation(null);
      setMessages([]);
      setExecution(null);
      setDraft("");
      setAttachments([]);
    }
    setView(next);
    setEditing(null);
    setDetail(null);
    setError("");
    setSearch("");
    setFilter("all");
    if (auth?.user && !auth.user.must_change_password)
      try {
        await refresh();
      } catch (e) {
        setError((e as Error).message);
      }
  }
  async function openTask(item: Conversation) {
    setBusy(true);
    setError("");
    try {
      const [data, runs] = await Promise.all([
        officeApi<Schema["OfficeMessages"]>(
          "/conversations/" + item.id + "/messages",
        ),
        officeApi<Execution[]>("/executions"),
      ]);
      history.replaceState(null, "", "#work/" + item.id);
      setConversation(item);
      setMessages(data.items);
      mergeFiles(data.items.flatMap((m) => m.files));
      setExecution(runs.find((r) => r.conversation_id === item.id) ?? null);
      setView("work");
      setDraft("");
      setAttachments([]);
      setDetail(null);
      setEditing(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function start(agent: Resource) {
    setBusy(true);
    setError("");
    try {
      const item = await officeApi<Conversation>("/conversations", {
        agent_id: agent.id,
        version: agent.published_version,
      });
      await refresh();
      await openTask(item);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function startGeneral() {
    const agent = agents.find((item) => item.id === generalAssistantId);
    if (agent) await start(agent);
    else setError("通用助手尚未加载，请刷新页面后重试。");
  }
  async function upload(selected: FileList | null) {
    if (!selected) return;
    if (
      attachments.length + selected.length >
      (auth?.limits.attachments ?? 5)
    ) {
      setError(
        "每次消息最多添加 " + (auth?.limits.attachments ?? 5) + " 个附件",
      );
      return;
    }
    setBusy(true);
    setError("");
    try {
      const uploaded: OfficeFile[] = [];
      for (const file of Array.from(selected)) {
        if (file.size > (auth?.limits.file_bytes ?? 20 * 1024 * 1024))
          throw new Error("单个附件超过允许大小");
        const body = new FormData();
        body.append("file", file);
        uploaded.push(await officeApi<OfficeFile>("/files", body));
      }
      mergeFiles(uploaded);
      setAttachments((old) => [...old, ...uploaded]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function send(e: React.FormEvent) {
    e.preventDefault();
    if (!conversation || !draft.trim() || conversationUnavailable) return;
    setBusy(true);
    setError("");
    try {
      const result = await officeApi<Execution>(
        "/conversations/" + conversation.id + "/messages",
        { content: draft.trim(), attachment_ids: attachments.map((f) => f.id) },
      );
      setExecution(result);
      setDraft("");
      setAttachments([]);
      const data = await officeApi<Schema["OfficeMessages"]>(
        "/conversations/" + conversation.id + "/messages",
      );
      setMessages(data.items);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function resourceState(resource: Resource, change: unknown) {
    try {
      const result = await officeApi<Resource>(
        "/" +
          (resource.kind === "skill" ? "skills" : "agents") +
          "/" +
          resource.id,
        change,
        "PATCH",
      );
      setDetail(result);
      await refresh();
    } catch (e) {
      setError((e as Error).message);
    }
  }
  if (!auth)
    return (
      <div className="of-loading" role="status">
        <img className="of-opencode-logo" src={openCodeLogo} width="117" height="21" alt="OpenCode" />
        <span>{error || "正在打开工作台…"}</span>
      </div>
    );
  if (!auth.user)
    return (
      <AuthForm
        initialized={auth.initialized}
        onAuthenticated={() => {
          history.replaceState(null, "", "#agents");
          setView("agents");
          void refreshAuth();
        }}
      />
    );
  const user = auth.user;
  const businessView = ["console", "tuning"].includes(view);
  const currentAgent = conversation
    ? agents.find((a) => a.id === conversation.agent_id)
    : null;
  const conversationUnavailable = !!conversation && (!currentAgent || currentAgent.archived ||
    (currentAgent.content as Agent).skills.some(ref => !skills.some(skill => skill.id === ref.id && !skill.archived)));
  const kind = view === "skills" ? "skill" : "agent";
  const resources = view === "skills" ? skills : agents;
  const filtered = resources.filter(
    (r) =>
      (filter === "archived" ? r.archived : !r.archived) &&
      (filter === "mine"
        ? r.owner_id === user.id
        : filter === "public"
          ? r.visibility === "public" && !r.builtin
          : filter === "builtin"
            ? r.builtin
            : true) &&
      (!search ||
        [r.content.title, r.content.description].some((v) =>
          v.toLowerCase().includes(search.toLowerCase()),
        )),
  );
  const title = editing
    ? editing.kind === "skill"
      ? "技能编辑"
      : "智能体编辑"
    : view === "work"
      ? (conversation?.agent_title ?? "我的任务")
      : view === "skills"
        ? "Skill 中心"
        : view === "tools"
          ? "办公工具"
          : view === "legacy"
            ? "旧版接待记录"
            : view === "console"
              ? "Monitor 诊断"
              : view === "tuning"
                ? "B 回路仿真调优"
                : view === "st-code"
                  ? "ST 代码优化"
                : view === "account"
                  ? "账号设置"
                  : view === "admin"
                    ? "账号管理"
                    : "智能体中心";
  const running = !!execution && !terminal.has(execution.status);
  return (
    <div
      className={"office-root" + (sidebarCollapsed ? " of-sidebar-collapsed" : "") + (sidebarResizing ? " of-sidebar-resizing" : "")}
      style={{ "--of-sidebar-width": sidebarWidth + "px" } as React.CSSProperties}
    >
      <aside id="office-sidebar" className="of-sidebar" aria-label="主导航">
        <div className="of-sidebar-scroll">
          <button className="of-brand" onClick={() => void navigate("agents")}>
            <span>◈</span>
            <strong>
              部门智能体
              <br />
              <small>工作台</small>
            </strong>
          </button>
          <nav aria-label="工作区">
            {navigation.map((item, i) => (
              <React.Fragment key={item.id}>
                {i === 4 && <span className="of-nav-caption">定制智能体</span>}
                <button
                  className={view === item.id || (view === "legacy" && item.id === "work") ? "active" : ""}
                  aria-current={view === item.id || (view === "legacy" && item.id === "work") ? "page" : undefined}
                  disabled={user.must_change_password}
                  onClick={() => void navigate(item.id)}
                >
                  <span aria-hidden="true">{item.mark}</span>
                  {item.label}
                </button>
              </React.Fragment>
            ))}
          </nav>
          {view === "work" && (
            <div className="of-task-nav">
              <div className="of-row">
                <small>历史任务</small>
                <button
                  className="of-link"
                  disabled={busy}
                  onClick={() => void startGeneral()}
                >
                  ＋ 新建
                </button>
              </div>
              {conversations.slice(0, 50).map((item) => (
                <div
                  className={
                    "of-task-row " +
                    (item.id === conversation?.id ? "active" : "")
                  }
                  key={item.id}
                >
                  <button onClick={() => void openTask(item)} title={item.title}>
                    {item.title}
                    <small>{item.agent_title}</small>
                  </button>
                  <button
                    className="of-task-menu"
                    aria-label={"重命名 " + item.title}
                    onClick={() => {
                      setRename(item);
                      setRenameText(item.title);
                    }}
                  >
                    ✎
                  </button>
                  <button
                    className="of-task-menu"
                    aria-label={"删除 " + item.title}
                    onClick={() => setConfirmDelete(item)}
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="of-profile">
          {user.is_admin && (
            <button onClick={() => void navigate("admin")}>账号管理</button>
          )}
          <button onClick={() => void navigate("account")}>
            <span className="of-avatar">{user.display_name.slice(0, 1)}</span>
            <span>
              {user.display_name}
              <small>{user.is_admin ? "平台管理员" : "部门成员"}</small>
            </span>
          </button>
          <button
            className="of-link"
            onClick={async () => {
              try {
                await officeApi("/auth/logout", undefined, "POST");
                history.replaceState(null, "", "#agents");
                setView("agents");
                setConversation(null);
                setMessages([]);
                setExecution(null);
                setFiles([]);
                setAttachments([]);
                await refreshAuth();
              } catch (e) {
                setError((e as Error).message);
              }
            }}
          >
            退出登录
          </button>
        </div>
        <div
          className="of-sidebar-resizer"
          role="separator"
          aria-label="调整主侧栏宽度"
          aria-controls="office-sidebar"
          aria-orientation="vertical"
          aria-valuemin={sidebarMinWidth}
          aria-valuemax={sidebarMaxWidth}
          aria-valuenow={sidebarWidth}
          tabIndex={0}
          title="拖动调整侧栏宽度；双击恢复默认宽度"
          onPointerDown={(e) => {
            if (e.button !== 0) return;
            e.preventDefault();
            e.currentTarget.setPointerCapture(e.pointerId);
            sidebarDrag.current = { pointerId: e.pointerId, x: e.clientX, width: sidebarWidth };
            setSidebarResizing(true);
          }}
          onPointerMove={(e) => {
            const drag = sidebarDrag.current;
            if (drag?.pointerId === e.pointerId)
              setSidebarWidth(clampSidebarWidth(drag.width + e.clientX - drag.x));
          }}
          onPointerUp={(e) => {
            if (e.currentTarget.hasPointerCapture(e.pointerId))
              e.currentTarget.releasePointerCapture(e.pointerId);
          }}
          onLostPointerCapture={() => {
            sidebarDrag.current = null;
            setSidebarResizing(false);
          }}
          onDoubleClick={() => setSidebarWidth(sidebarDefaultWidth)}
          onKeyDown={(e) => {
            const width = e.key === "ArrowLeft" ? sidebarWidth - 20
              : e.key === "ArrowRight" ? sidebarWidth + 20
              : e.key === "Home" ? sidebarMinWidth
              : e.key === "End" ? sidebarMaxWidth : null;
            if (width !== null) {
              e.preventDefault();
              setSidebarWidth(clampSidebarWidth(width));
            }
          }}
        />
      </aside>
      <main className="of-main">
        <header className="of-topbar">
          <div className="of-top-heading">
            <button
              className="of-sidebar-toggle"
              aria-label={sidebarCollapsed ? "展开主侧栏" : "收起主侧栏"}
              aria-controls="office-sidebar"
              aria-expanded={!sidebarCollapsed}
              title={sidebarCollapsed ? "展开主侧栏" : "收起主侧栏"}
              onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
            >
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                <rect x="3" y="4" width="18" height="16" rx="3" stroke="currentColor" strokeWidth="1.6" />
                <path d="M9 4v16" stroke="currentColor" strokeWidth="1.6" />
              </svg>
            </button>
            <div className="of-heading-text">
              <span className="of-breadcrumb">工作台 / {title}</span>
              <h1>{title}</h1>
            </div>
          </div>
          <div
            className={
              "of-top-actions" +
              (view === "work" && conversation ? " has-task" : "")
            }
          >
            {view === "work" && conversation && (
              <>
                <button
                  onClick={() =>
                    currentAgent
                      ? void start(currentAgent)
                      : void navigate("agents")
                  }
                >
                  新建任务
                </button>
                <button onClick={() => void navigate("agents")}>
                  更换智能体
                </button>
              </>
            )}
            <img className="of-opencode-logo" src={openCodeLogo} width="117" height="21" alt="OpenCode" />
            <span className={"of-model " + (modelReady ? "ready" : "")}>
              {modelReady === null
                ? "连接中"
                : modelReady
                  ? "模型可用"
                  : "模型未就绪"}
            </span>
          </div>
        </header>
        {error && (
          <div className="of-error-wrap">
            <p className="error" role="alert">
              {error}
              <button onClick={() => setError("")}>关闭</button>
            </p>
          </div>
        )}
        <div
          className={
            "of-content " +
            (editing
              ? "of-editor-content"
              : view === "legacy"
              ? "of-history-content"
              : businessView
              ? "of-business-content"
              : view === "work" && conversation
                ? "of-chat-content"
                : "")
          }
        >
          {user.must_change_password || view === "account" ? (
            <AccountPanel user={user} onChanged={() => void refreshAuth()} />
          ) : view === "admin" ? (
            <AdminPanel />
          ) : editing ? (
            <ResourceEditor
              key={
                editing.resource?.id ?? editing.initialSkill?.id ?? editing.kind
              }
              {...editing}
              skills={skills}
              tools={tools}
              onClose={() => setEditing(null)}
              onSaved={(r) => {
                setEditing(null);
                setDetail(r);
                setView(r.kind === "skill" ? "skills" : "agents");
                void refresh();
              }}
            />
          ) : view === "legacy" ? (
            <LegacyHistory onBack={() => void navigate("work")} onNew={() => void startGeneral()} busy={busy} />
          ) : businessView ? (
            <div className="of-business">
              {renderLegacy(
                view as "console" | "tuning",
                (v) => void navigate(v === "chat" ? "legacy" : v as View),
              )}
            </div>
          ) : view === "st-code" ? (
            <STWorkspace />
          ) : view === "tools" ? (
            <ToolWorkspace
              tools={tools}
              files={files}
              onFiles={mergeFiles}
              isAdmin={user.is_admin}
              onRefresh={() => void refresh()}
            />
          ) : view === "work" ? (
            conversation ? (
              <>
                <div
                  className="of-chat-scroll"
                  ref={chatScroll.transcript}
                  onScroll={chatScroll.onScroll}
                >
                  <div className="of-chat-inner" ref={chatScroll.content}>
                    <div className="of-task-intro">
                      <span className="of-agent-symbol">◈</span>
                      <h2>{conversation.agent_title}</h2>
                      <p>
                        {conversation.greeting ||
                          "上传材料，描述你的任务，我会根据已配置的技能为你处理。"}
                      </p>
                      <small>
                        任务绑定版本 v{conversation.agent_version} ·
                        文件与对话仅自己可见
                      </small>
                      {messages.length === 0 && (
                        <div className="of-examples">
                          {(conversation.examples ?? []).map((example) => (
                            <button
                              key={example}
                              onClick={() => {
                                setDraft(example);
                                composer.current?.focus();
                              }}
                            >
                              {example} <span>↗</span>
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                    {messages.map((message) => (
                      <article
                        className={"of-message " + message.role}
                        key={message.id}
                      >
                        <span className="of-message-avatar">
                          {message.role === "user"
                            ? user.display_name.slice(0, 1)
                            : "◈"}
                        </span>
                        <div>
                          <strong>
                            {message.role === "user"
                              ? user.display_name
                              : conversation.agent_title}
                          </strong>
                          <div className="of-message-body">
                            <Markdown
                              remarkPlugins={[remarkGfm]}
                              components={{ img: () => null }}
                            >
                              {message.content}
                            </Markdown>
                          </div>
                          <FileCards files={message.files} />
                          <small className="of-muted">
                            {new Date(message.created_at).toLocaleTimeString()}
                          </small>
                        </div>
                      </article>
                    ))}
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
                </div>
                {chatScroll.showLatest && (
                  <button
                    className="of-latest"
                    onClick={chatScroll.scrollToLatest}
                  >
                    ↓ 回到最新消息
                  </button>
                )}
                <form className="of-composer" onSubmit={send}>
                  {conversationUnavailable && <p className="of-muted" role="status">此任务的智能体或依赖 Skill 已归档、删除或不再共享。历史消息和文件仍可查阅，请选择可用智能体新建任务。</p>}
                  <div className="of-attachment-chips">
                    {attachments.map((file) => (
                      <span key={file.id}>
                        {file.name}
                        <button
                          type="button"
                          aria-label={"移除 " + file.name}
                          onClick={() =>
                            setAttachments((old) =>
                              old.filter((f) => f.id !== file.id),
                            )
                          }
                        >
                          ×
                        </button>
                      </span>
                    ))}
                  </div>
                  <textarea
                    ref={composer}
                    aria-label="任务要求"
                    disabled={conversationUnavailable}
                    rows={3}
                    maxLength={12000}
                    placeholder="描述任务，或添加需要处理的文件……"
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
                        e.preventDefault();
                        e.currentTarget.form?.requestSubmit();
                      }
                    }}
                  />
                  <div className="of-row">
                    <label className="of-upload-button">
                      ＋ 添加附件
                      <input
                        type="file"
                        multiple
                        accept=".docx,.xlsx,.csv,.txt,.md,.pdf"
                        disabled={busy || running || conversationUnavailable}
                        onChange={(e) => {
                          void upload(e.target.files);
                          e.target.value = "";
                        }}
                      />
                    </label>
                    <small>
                      最多 {auth.limits.attachments} 个文件 · 单个不超过{" "}
                      {Math.floor(auth.limits.file_bytes / 1024 / 1024)} MiB
                    </small>
                    <button
                      className="of-primary"
                      disabled={busy || running || conversationUnavailable || !draft.trim()}
                    >
                      {busy ? "处理中…" : running ? "任务处理中" : "发送 ↑"}
                    </button>
                  </div>
                </form>
              </>
            ) : (
              <div>
                <div className="of-section-heading">
                  <div>
                    <span className="of-eyebrow">继续工作</span>
                    <h2>我的任务</h2>
                    <p>每个任务保留其智能体、附件和生成结果。</p>
                  </div>
                  <div className="of-history-actions">
                    <button onClick={() => void navigate("legacy")}>旧版接待记录</button>
                    <button onClick={() => void navigate("agents")}>选择智能体</button>
                    <button className="of-primary" disabled={busy} onClick={() => void startGeneral()}>新建对话</button>
                  </div>
                </div>
                <div className="of-cards">
                  {conversations.map((item) => (
                    <article className="of-resource-card" key={item.id}>
                      <span className="of-card-symbol">▤</span>
                      <h3>{item.title}</h3>
                      <p>{item.agent_title}</p>
                      <small>
                        {new Date(item.created_at).toLocaleString()}
                      </small>
                      <div className="of-card-actions">
                        <button
                          onClick={() => {
                            setRename(item);
                            setRenameText(item.title);
                          }}
                        >
                          重命名
                        </button>
                        <button onClick={() => setConfirmDelete(item)}>
                          删除
                        </button>
                        <button
                          className="of-primary"
                          onClick={() => void openTask(item)}
                        >
                          继续任务 ↗
                        </button>
                      </div>
                    </article>
                  ))}
                </div>
                {!conversations.length && (
                  <div className="of-empty">
                    还没有任务。可以直接与通用助手聊天，也可以选择专用智能体处理材料。
                  </div>
                )}
              </div>
            )
          ) : (
            <>
              <section className="of-welcome">
                <div>
                  <span className="of-eyebrow">
                    {view === "skills"
                      ? "沉淀你的工作经验"
                      : "为手头的工作，选择一位助手"}
                  </span>
                  <h2>
                    {view === "skills"
                      ? "把熟悉的流程，变成可复用的技能。"
                      : "从工作材料，到一份完成的成果。"}
                  </h2>
                  <p>
                    {view === "skills"
                      ? "写下步骤、选择工具，再把技能交给智能体执行。"
                      : "选择公共助手直接使用，也可以组合技能创建自己的智能体。"}
                  </p>
                </div>
                <button
                  className="of-primary"
                  onClick={() => {
                    setEditing({ kind });
                    setDetail(null);
                  }}
                >
                  ＋ 创建{view === "skills" ? " Skill" : "智能体"}
                </button>
              </section>
              <div className="of-list-toolbar">
                <div className="of-tabs" role="tablist" aria-label="资源范围">
                  {[
                    ["all", "全部"],
                    ["mine", "我的"],
                    ["public", "公共"],
                    ["builtin", "平台内置"],
                    ["archived", "已归档"],
                  ].map(([id, label]) => (
                    <button
                      role="tab"
                      aria-selected={filter === id}
                      className={filter === id ? "active" : ""}
                      key={id}
                      onClick={() => setFilter(id)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <input
                  type="search"
                  aria-label="搜索资源"
                  value={search}
                  placeholder="搜索名称或用途"
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>
              <div
                className={"of-resource-layout " + (detail ? "has-detail" : "")}
              >
                <div className="of-cards">
                  {filtered.map((resource) => (
                    <article className="of-resource-card" key={resource.id}>
                      <div className="of-row">
                        <span className="of-card-symbol">
                          {resource.kind === "skill" ? "◇" : "◈"}
                        </span>
                        <span className="of-badge">
                          {resource.builtin
                            ? "平台内置"
                            : resource.visibility === "public"
                              ? "公共"
                              : "私有"}
                        </span>
                      </div>
                      <h3>{resource.content.title}</h3>
                      <p>{resource.content.description}</p>
                      <div className="of-card-meta">
                        <span>
                          {resource.published_version
                            ? "已发布 v" + resource.published_version
                            : "草稿"}
                        </span>
                        <span>
                          {resource.builtin
                            ? "平台提供"
                            : resource.owner_id === user.id
                              ? "我创建的"
                              : "可使用"}
                        </span>
                      </div>
                      <div className="of-card-actions">
                        <button onClick={() => setDetail(resource)}>
                          查看详情
                        </button>
                        {resource.kind === "agent" ? (
                          <button
                            className="of-primary"
                            disabled={
                              busy ||
                              !resource.published_version ||
                              resource.archived
                            }
                            onClick={() => void start(resource)}
                          >
                            开始使用 ↗
                          </button>
                        ) : (
                          <button
                            className="of-primary"
                            disabled={
                              !resource.published_version || resource.archived
                            }
                            onClick={() =>
                              setEditing({
                                kind: "agent",
                                initialSkill: resource,
                              })
                            }
                          >
                            创建智能体 ↗
                          </button>
                        )}
                      </div>
                    </article>
                  ))}
                </div>
                {!filtered.length && (
                  <div className="of-empty">
                    这里还没有匹配的{view === "skills" ? "技能" : "智能体"}
                    。可以创建一个，或切换筛选条件。
                  </div>
                )}
                {detail && (
                  <aside className="of-resource-detail">
                    <div className="of-row">
                      <span className="of-eyebrow">
                        {detail.kind === "skill" ? "技能详情" : "智能体详情"}
                      </span>
                      <button
                        className="of-link"
                        onClick={() => setDetail(null)}
                      >
                        关闭 ×
                      </button>
                    </div>
                    <h2>{detail.content.title}</h2>
                    <p>{detail.content.description}</p>
                    <div className="of-card-meta">
                      <span>
                        {detail.builtin
                          ? "平台内置"
                          : detail.visibility === "public"
                            ? "公共"
                            : "仅自己可见"}
                      </span>
                      <span>
                        {detail.published_version
                          ? "发布版本 v" + detail.published_version
                          : "尚未发布"}
                      </span>
                    </div>
                    {detail.builtin && (
                      <p>平台提供，所有成员可使用。任务、附件和产物仅自己可见。</p>
                    )}
                    {detail.archived && <p className="of-callout">此资源已归档，其他成员无法查看、引用或使用。你可以恢复资源，也可以删除。</p>}
                    <h3>工作要求</h3>
                    <div className="of-detail-markdown">
                      <Markdown
                        remarkPlugins={[remarkGfm]}
                        components={{ img: () => null }}
                      >
                        {detail.content.instructions ||
                          "根据所选技能处理任务。"}
                      </Markdown>
                    </div>
                    {detail.kind === "skill" && (
                      <>
                        {(detail.content as Skill).inputs && (
                          <>
                            <h3>输入要求</h3>
                            <p>{(detail.content as Skill).inputs}</p>
                          </>
                        )}
                        {(detail.content as Skill).output_format && (
                          <>
                            <h3>输出要求</h3>
                            <p>{(detail.content as Skill).output_format}</p>
                          </>
                        )}
                      </>
                    )}
                    {detail.kind === "agent" && (
                      <>
                        <h3>绑定的 Skill</h3>
                        <div className="of-chips">
                          {(detail.content as Agent).skills.map((ref) => (
                            <span key={ref.id}>
                              {skills.find((s) => s.id === ref.id)?.content
                                .title ?? "不可用的技能"}{" "}
                              · v{ref.version}
                            </span>
                          ))}
                        </div>
                      </>
                    )}
                    <h3>依赖工具</h3>
                    <div className="of-chips">
                      {detail.content.tools.length ? (
                        detail.content.tools.map((id) => (
                          <span key={id}>
                            {tools.find((t) => t.id === id)?.title ?? id}
                          </span>
                        ))
                      ) : (
                        <small>未指定工具</small>
                      )}
                    </div>
                    {detail.editable && (
                      <div className="of-detail-actions">
                        <button
                          disabled={detail.archived}
                          onClick={() =>
                            setEditing({ kind: detail.kind, resource: detail })
                          }
                        >
                          编辑草稿
                        </button>
                        {!detail.archived && (
                          <button
                            onClick={() =>
                              void resourceState(detail, {
                                visibility:
                                  detail.visibility === "public"
                                    ? "private"
                                    : "public",
                              })
                            }
                          >
                            {detail.visibility === "public"
                              ? "设为私有"
                              : "设为公共"}
                          </button>
                        )}
                        <button
                          onClick={() =>
                            void resourceState(detail, {
                              archived: !detail.archived,
                            })
                          }
                        >
                          {detail.archived ? "恢复资源" : "归档资源"}
                        </button>
                        <button className="of-danger" onClick={() => { setResourceDeleteError(""); setResourceToDelete(detail); }}>删除资源</button>
                      </div>
                    )}
                  </aside>
                )}
              </div>
            </>
          )}
        </div>
      </main>
      {resourceToDelete && <dialog ref={resourceModal} className="of-modal" aria-label="删除资源" onCancel={e => { if (resourceDeleting) e.preventDefault(); else setResourceToDelete(null); }}>
        <h2>删除{resourceToDelete.kind === "skill" ? " Skill" : "智能体"}</h2>
        <p>确认删除“{resourceToDelete.content.title}”？资源及其发布版本删除后无法恢复。</p>
        <p>历史任务、消息和文件会保留。使用已删除智能体的旧任务仅供查阅，不能继续执行。</p>
        {resourceToDelete.kind === "skill" && <p>仍被智能体草稿或发布版本引用的 Skill 不能删除。可先删除关联智能体，或选择归档以停用并保留引用。</p>}
        {resourceDeleteError && <p className="error" role="alert">{resourceDeleteError}</p>}
        <div className="of-actions">
          <button disabled={resourceDeleting} onClick={() => setResourceToDelete(null)}>取消</button>
          <button className="of-danger" disabled={resourceDeleting} onClick={async () => {
            setResourceDeleting(true); setResourceDeleteError("");
            try {
              await officeApi("/" + (resourceToDelete.kind === "skill" ? "skills" : "agents") + "/" + resourceToDelete.id, undefined, "DELETE");
              setDetail(null); setResourceToDelete(null); await refresh();
            } catch (e) { setResourceDeleteError((e as Error).message); }
            finally { setResourceDeleting(false); }
          }}>{resourceDeleting ? "删除中…" : "确认删除"}</button>
        </div>
      </dialog>}
      {(confirmDelete || rename) && (
        <dialog
          ref={modal}
          className="of-modal"
          onCancel={() => {
            setRename(null);
            setConfirmDelete(null);
          }}
          aria-label={rename ? "重命名任务" : "删除任务"}
        >
          <h2>{rename ? "重命名任务" : "删除任务"}</h2>
          {rename ? (
            <form
              onSubmit={async (e) => {
                e.preventDefault();
                try {
                  await officeApi(
                    "/conversations/" + rename.id,
                    { title: renameText },
                    "PATCH",
                  );
                  if (conversation?.id === rename.id)
                    setConversation({ ...conversation, title: renameText });
                  setRename(null);
                  await refresh();
                } catch (e) {
                  setError((e as Error).message);
                }
              }}
            >
              <label>
                任务名称
                <input
                  autoFocus
                  required
                  maxLength={80}
                  value={renameText}
                  onChange={(e) => setRenameText(e.target.value)}
                />
              </label>
              <div className="of-actions">
                <button type="button" onClick={() => setRename(null)}>
                  取消
                </button>
                <button className="of-primary">保存</button>
              </div>
            </form>
          ) : (
            <>
              <p>
                确认删除“{confirmDelete?.title}
                ”的对话记录？正在执行的任务需要先取消。
              </p>
              <div className="of-actions">
                <button onClick={() => setConfirmDelete(null)}>保留任务</button>
                <button
                  className="of-danger"
                  onClick={async () => {
                    try {
                      await officeApi(
                        "/conversations/" + confirmDelete!.id,
                        undefined,
                        "DELETE",
                      );
                      if (conversation?.id === confirmDelete!.id) {
                        setConversation(null);
                        setMessages([]);
                        setExecution(null);
                      }
                      setConfirmDelete(null);
                      await refresh();
                    } catch (e) {
                      setError((e as Error).message);
                      setConfirmDelete(null);
                    }
                  }}
                >
                  删除任务
                </button>
              </div>
            </>
          )}
        </dialog>
      )}
    </div>
  );
}
