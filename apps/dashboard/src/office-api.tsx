import type { components } from "./api.generated";

export type Schema = components["schemas"];
export type Resource = Schema["ResourceInfo"];
export type Skill = Schema["SkillContent"];
export type Agent = Schema["AgentContent"];
export type OfficeFile = Schema["FileInfo"];
export type Execution = Schema["ExecutionInfo"];
export type User = Schema["UserInfo"];
export type Conversation = Schema["ConversationInfo"];
export type OfficeMessage = Schema["OfficeMessage"];
export type Tool = Schema["ToolInfo"];

export class ApiError extends Error {
  constructor(
    message: string,
    public status: number,
  ) {
    super(message);
  }
}
export async function officeApi<T>(
  path: string,
  body?: unknown,
  method?: string,
  key?: string,
): Promise<T> {
  const headers: Record<string, string> = {};
  const write = method ? method !== "GET" : body !== undefined;
  if (write) {
    const auth = await fetch("/api/v1/auth/me").then((r) => r.json());
    headers["X-CSRF-Token"] = auth.csrf_token;
    headers["Idempotency-Key"] =
      key ??
      crypto.randomUUID?.() ??
      Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) =>
        b.toString(16).padStart(2, "0"),
      ).join("");
    if (!(body instanceof FormData))
      headers["Content-Type"] = "application/json";
  }
  const response = await fetch("/api/v1" + path, {
    method: method ?? (write ? "POST" : "GET"),
    headers,
    body:
      body === undefined
        ? undefined
        : body instanceof FormData
          ? body
          : JSON.stringify(body),
  });
  const data = await response.json();
  if (!response.ok) {
    if (response.status === 401 && !path.startsWith("/auth/"))
      window.dispatchEvent(new Event("office-auth-expired"));
    throw new ApiError(data.error?.message ?? "请求失败", response.status);
  }
  return data;
}

export function FileCards({ files }: { files: OfficeFile[] }) {
  return (
    <div className="of-files">
      {files.map((file) => (
        <a className="of-file" href={file.download_url} key={file.id} download>
          <span className="of-file-icon">
            {file.name.split(".").pop()?.toUpperCase()}
          </span>
          <span>
            <strong>{file.name}</strong>
            <small>
              {file.size < 1024
                ? file.size + " B"
                : (file.size / 1024).toFixed(1) + " KB"}{" "}
              · 下载文件
            </small>
          </span>
          <span aria-hidden="true">↓</span>
        </a>
      ))}
    </div>
  );
}

export const terminal = new Set([
  "succeeded",
  "failed",
  "cancelled",
  "interrupted",
]);
export const executionLabels: Record<string, string> = {
  queued: "排队中",
  running: "处理中",
  succeeded: "已完成",
  failed: "处理失败",
  cancelled: "已取消",
  interrupted: "已中断",
};
