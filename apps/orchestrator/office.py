"""Durable office executions with one worker and per-execution tool capabilities."""

import asyncio
import hashlib
import json
import logging
import re
import secrets
import time

from sqlalchemy import select

from packages.adapters.office_files import FileService, ToolRunner
from packages.adapters.office_resources import ResourceService
from packages.adapters.platform_store import conversations, executions, messages
from packages.contracts.common import now, uid
from packages.contracts.office import ExecutionInfo
from packages.domain.core import DomainError

TERMINAL = {"succeeded", "failed", "cancelled", "interrupted"}
logger = logging.getLogger("office")


def verified_reply(text, output_files):
    """A completion claim cannot substitute for a registered artifact."""
    creation = r"(?:生成|导出|创建|保存|制作)"
    artifact = r"(?:文件|文档|周报|报告|报表|表格|清单|Word|Excel|DOCX|XLSX|PDF|CSV|Markdown)"
    claims_file = re.search(
        rf"(?:已(?:经)?|成功)[^。！？\n]{{0,48}}{creation}[^。！？\n]{{0,24}}{artifact}"
        rf"|{artifact}[^。！？\n]{{0,16}}(?:已(?:经)?|成功){creation}"
        rf"|{creation}(?:了|完成|成功)[^。！？\n]{{0,24}}{artifact}",
        text,
        re.IGNORECASE,
    )
    # Model-authored remote download locations are never registered file handles.
    remote_artifact = re.search(
        r"https?://[^\s<>）)]+\.(?:docx?|xlsx?|csv|txt|md|pdf|zip)(?:[?#][^\s<>）)]*)?",
        text,
        re.IGNORECASE,
    )
    if not output_files and (claims_file or remote_artifact):
        raise DomainError(
            "ARTIFACT_NOT_CREATED",
            "模型声称生成了文件，但本轮未登记真实产物。请重新生成，不能使用未验证的下载地址。",
            503,
        )
    if remote_artifact:
        return "已生成以下文件，请通过下方文件卡片下载：" + "、".join(f.name for f in output_files)
    return text


class OfficeEngine:
    def __init__(self, platform, client):
        self.p, self.client = platform, client
        self.files = FileService(platform)
        self.runner = ToolRunner(platform, self.files)
        self.resources = ResourceService(platform)
        self.wake = asyncio.Event()
        self.scopes = {}
        self.active = {}
        self.tool_tasks = {}
        with self.p.engine.begin() as c:
            for row in (
                c.execute(select(executions).where(executions.c.status == "running"))
                .mappings()
                .all()
            ):
                d = {**row["data"], "error": "服务重启中断了任务，请核对已有结果后重新发起。"}
                c.execute(
                    executions.update()
                    .where(executions.c.id == row["id"])
                    .values(status="interrupted", data=d)
                )

    def get_row(self, execution_id, user=None):
        with self.p.engine.connect() as c:
            q = select(executions).where(executions.c.id == execution_id)
            if user is not None:
                q = q.where(executions.c.owner_id == user)
            row = c.execute(q).mappings().first()
        if not row:
            raise DomainError("NOT_FOUND", status=404)
        return dict(row)

    def info(self, execution_id, user):
        row = self.get_row(execution_id, user)
        d = row["data"]
        return ExecutionInfo(
            id=execution_id,
            kind=d["kind"],
            conversation_id=d.get("conversation_id"),
            status=row["status"],
            created_at=d["created_at"],
            events=d["events"],
            files=self.files.list_execution(execution_id, user),
            result=d.get("result"),
            error=d.get("error"),
        )

    def update(self, execution_id, status=None, **values):
        with self.p.engine.begin() as c:
            row = (
                c.execute(select(executions).where(executions.c.id == execution_id))
                .mappings()
                .one()
            )
            c.execute(
                executions.update()
                .where(executions.c.id == execution_id)
                .values(status=status or row["status"], data={**row["data"], **values})
            )

    def event(self, execution_id, label, **detail):
        row = self.get_row(execution_id)
        events = row["data"]["events"]
        events.append(dict(time=now(), label=label, **detail))
        self.update(execution_id, events=events[-100:])

    def create(self, user, kind, payload, conversation_id, conn):
        execution_id = uid()
        data = dict(
            kind=kind,
            payload=payload,
            conversation_id=conversation_id,
            created_at=now(),
            events=[dict(time=now(), label="任务已排队")],
            error=None,
            result=None,
            session_id=None,
        )
        conn.execute(
            executions.insert().values(id=execution_id, owner_id=user, status="queued", data=data)
        )
        return execution_id

    def conversation(self, conversation_id, user, conn):
        row = (
            conn.execute(
                select(conversations).where(
                    conversations.c.id == conversation_id, conversations.c.owner_id == user
                )
            )
            .mappings()
            .first()
        )
        if not row:
            raise DomainError("NOT_FOUND", status=404)
        return dict(row["data"])

    async def cancel(self, execution_id, user):
        row = self.get_row(execution_id, user)
        if row["status"] in TERMINAL:
            return self.info(execution_id, user)
        self.update(execution_id, "cancelled", error="任务已取消")
        for scope in self.scopes.values():
            if scope["execution_id"] == execution_id:
                scope["revoked"] = True
        task = self.active.get(execution_id)
        if task:
            task.cancel()
        if row["data"].get("session_id"):
            await self.client.abort(row["data"]["session_id"])
        for task in list(self.tool_tasks.get(execution_id, set())):
            task.cancel()
        self.wake.set()
        return self.info(execution_id, user)

    async def call(self, token, tool, arguments):
        scope = self.scopes.get(hashlib.sha256(token.encode()).hexdigest())
        if (
            not scope
            or scope["revoked"]
            or scope["expires"] < time.monotonic()
            or tool not in scope["tools"]
        ):
            raise DomainError("TOOL_FORBIDDEN", "工具调用未授权或已过期", 403)
        self.p.get_user(scope["user"])
        self.resources.snapshot(scope["agent_id"], scope["version"], scope["user"])
        if token in json.dumps(arguments, ensure_ascii=False):
            raise DomainError("TOOL_FORBIDDEN", "工具内容不能包含执行凭证", 403)
        if self.get_row(scope["execution_id"])["status"] != "running":
            raise DomainError("TOOL_FORBIDDEN", status=403)
        scope["calls"] += 1
        if scope["calls"] > 24:
            raise DomainError("TOOL_BUDGET_EXCEEDED", "超过本轮工具调用次数上限", 409)
        key = hashlib.sha256(
            json.dumps([tool, arguments], sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        if key in scope["results"]:
            return scope["results"][key]
        from packages.adapters.office_tools import TOOL_REGISTRY

        self.event(scope["execution_id"], "正在" + TOOL_REGISTRY[tool][0], tool=tool)
        task = asyncio.create_task(
            self.runner.run(
                tool,
                arguments,
                scope["user"],
                scope["execution_id"],
                scope["conversation_id"],
                scope["files"],
            )
        )
        self.tool_tasks.setdefault(scope["execution_id"], set()).add(task)
        try:
            result = await task
            scope["files"].update(f["id"] for f in result["files"])
            scope["results"][key] = result
            self.event(scope["execution_id"], "已完成" + TOOL_REGISTRY[tool][0], tool=tool)
            return result
        except DomainError as exc:
            self.event(scope["execution_id"], "工具执行失败：" + exc.message, tool=tool)
            raise
        finally:
            self.tool_tasks[scope["execution_id"]].discard(task)

    async def execute(self, row):
        execution_id, user, data = row["id"], row["owner_id"], row["data"]
        self.update(execution_id, "running")
        token = None
        try:
            self.p.get_user(user)
            if data["kind"] == "tool":
                payload = data["payload"]
                self.event(execution_id, "正在执行工具", tool=payload["tool"])
                result = await self.runner.run(
                    payload["tool"], payload["arguments"], user, execution_id
                )
            else:
                payload, conversation_id = data["payload"], data["conversation_id"]
                snapshot = self.resources.snapshot(payload["agent_id"], payload["version"], user)
                token = secrets.token_urlsafe(40)
                allowed = self.files.conversation_ids(conversation_id, user)
                scope = dict(
                    execution_id=execution_id,
                    agent_id=payload["agent_id"],
                    version=payload["version"],
                    user=user,
                    conversation_id=conversation_id,
                    tools=set(snapshot["content"]["tools"]),
                    files=allowed,
                    calls=0,
                    expires=time.monotonic() + 600,
                    revoked=False,
                    results={},
                )
                self.scopes[hashlib.sha256(token.encode()).hexdigest()] = scope
                for skill in snapshot["skills"]:
                    self.event(
                        execution_id, "已加载 Skill：" + skill["title"], version=skill["version"]
                    )
                with self.p.engine.connect() as c:
                    history = [
                        r["data"]
                        for r in c.execute(
                            select(messages).where(messages.c.conversation_id == conversation_id)
                        ).mappings()
                    ]
                history.sort(key=lambda m: (m["created_at"], m["id"]))
                history = history[-10:]
                file_context = []
                for file_id in sorted(allowed):
                    file_row, _ = self.files.require(file_id, user)
                    file_context.append(self.files.info(file_row).model_dump())

                def on_session(session):
                    self.update(execution_id, session_id=session)

                text = await self.client.office_chat(
                    snapshot, history, file_context, token, on_session
                )
                # An owner may archive a resource or revoke sharing while inference
                # is in progress. Do not commit a reply under an obsolete permission.
                self.resources.snapshot(payload["agent_id"], payload["version"], user)
                text = text.replace(token, "[已隐藏执行凭证]")
                output_files = self.files.list_execution(execution_id, user)
                text = verified_reply(text, output_files)
                message = dict(
                    id=uid(),
                    role="assistant",
                    content=text,
                    files=[f.model_dump() for f in output_files],
                    execution_id=execution_id,
                    created_at=now(),
                )
                with self.p.engine.begin() as c:
                    c.execute(
                        messages.insert().values(
                            id=message["id"], conversation_id=conversation_id, data=message
                        )
                    )
                result = {"message": text}
            if self.get_row(execution_id)["status"] != "cancelled":
                self.event(execution_id, "任务已完成")
                self.update(execution_id, "succeeded", result=result)
        except asyncio.CancelledError:
            if self.get_row(execution_id)["status"] != "cancelled":
                self.update(execution_id, "interrupted", error="任务执行被中断")
            raise
        except asyncio.TimeoutError:
            self.update(execution_id, "failed", error="任务超过时限，请缩小材料或拆分任务")
        except Exception as exc:
            if not isinstance(exc, DomainError):
                logger.exception("Office execution %s failed", execution_id)
            self.update(
                execution_id,
                "failed",
                error=exc.message
                if isinstance(exc, DomainError)
                else "任务处理失败，请检查材料后重试",
            )
        finally:
            if token:
                self.scopes.pop(hashlib.sha256(token.encode()).hexdigest(), None)
            current = self.get_row(execution_id)
            if current["status"] != "succeeded" and current["data"].get("session_id"):
                await self.client.abort(current["data"]["session_id"])
            pending = list(self.tool_tasks.pop(execution_id, set()))
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

    async def serve(self):
        while True:
            with self.p.engine.connect() as c:
                rows = (
                    c.execute(select(executions).where(executions.c.status == "queued"))
                    .mappings()
                    .all()
                )
            row = min(rows, key=lambda r: r["data"]["created_at"]) if rows else None
            if row:
                task = asyncio.create_task(asyncio.wait_for(self.execute(dict(row)), timeout=600))
                self.active[row["id"]] = task
                try:
                    await task
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():
                        raise
                except asyncio.TimeoutError:
                    self.update(row["id"], "failed", error="任务超过 10 分钟时限")
                finally:
                    self.active.pop(row["id"], None)
            else:
                self.wake.clear()
                try:
                    await asyncio.wait_for(self.wake.wait(), timeout=2)
                except asyncio.TimeoutError:
                    pass
