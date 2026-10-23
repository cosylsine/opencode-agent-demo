"""Departmental account and workspace HTTP endpoints."""

import hashlib
import hmac
import os
import secrets
import time

from fastapi import Depends, Request, Response, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select

from packages.adapters.office_tools import TOOL_REGISTRY, validate_arguments
from packages.adapters.platform_store import (
    conversations,
    executions,
    files,
    messages,
    tool_settings,
    user_public,
    users,
)
from packages.contracts.common import now, uid
from packages.contracts.office import (
    AgentWrite,
    AuthInfo,
    ConversationCreate,
    ConversationInfo,
    ConversationList,
    ConversationRename,
    Credentials,
    ExecutionInfo,
    FileInfo,
    InternalOfficeCall,
    MessageCreate,
    OfficeMessages,
    PasswordChange,
    Registration,
    ResourceInfo,
    ResourceList,
    ResourceState,
    SkillWrite,
    ToolInfo,
    ToolList,
    ToolRun,
    ToolState,
    UserInfo,
    UserUpdate,
)
from packages.domain.core import DomainError


def install_routes(app, identity, writer, write_idempotent, lan_peer_allowed):
    def limits():
        return {
            "file_bytes": int(os.getenv("OFFICE_MAX_FILE_BYTES", "20971520")),
            "attachments": max(1, min(20, int(os.getenv("OFFICE_MAX_ATTACHMENTS", "5")))),
        }

    def anonymous_csrf(request, nonce):
        return hmac.new(app.state.csrf_secret, ("anon:" + nonce).encode(), "sha256").hexdigest()

    def auth_network(request):
        if not lan_peer_allowed(request.client.host if request.client else ""):
            raise DomainError("UNAUTHENTICATED", "客户端不在允许访问的网段内", 401)

    def auth_write(request):
        auth_network(request)
        if not app.state.origin_allowed(request.headers.get("origin")):
            raise DomainError("CSRF_REJECTED", status=403)
        session = app.state.platform.session(request.cookies.get("office_session"))
        expected = (
            session[1]
            if session
            else anonymous_csrf(request, request.cookies.get("office_anon", ""))
        )
        if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), expected):
            raise DomainError("CSRF_REJECTED", status=403)

    def admin(user):
        if not app.state.platform.get_user(user)["is_admin"]:
            raise DomainError("FORBIDDEN", "此操作需要管理员权限", 403)

    @app.get("/api/v1/auth/me", response_model=AuthInfo)
    async def me(request: Request, response: Response):
        auth_network(request)
        session = app.state.platform.session(request.cookies.get("office_session"))
        if session:
            return dict(
                schema_version="1.0",
                user=user_public(session[0]),
                csrf_token=session[1],
                initialized=True,
                limits=limits(),
            )
        nonce = request.cookies.get("office_anon") or secrets.token_urlsafe(32)
        response.set_cookie(
            "office_anon",
            nonce,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            max_age=43200,
        )
        return dict(
            schema_version="1.0",
            user=None,
            csrf_token=anonymous_csrf(request, nonce),
            initialized=app.state.platform.initialized(),
            limits=limits(),
        )

    @app.post("/api/v1/auth/register", response_model=UserInfo, status_code=201)
    async def register(body: Registration, request: Request):
        auth_write(request)
        if not app.state.platform.initialized():
            raise DomainError(
                "SETUP_REQUIRED", "请管理员先执行 deploy.sh init-admin 初始化账号", 409
            )
        return app.state.platform.create_user(body.username, body.display_name, body.password)

    @app.post("/api/v1/auth/login", response_model=AuthInfo)
    async def login(body: Credentials, request: Request, response: Response):
        auth_write(request)
        key = (request.client.host, body.username.strip().lower())
        attempts = app.state.auth_rate.setdefault(key, [])
        attempts[:] = [t for t in attempts if t > time.monotonic() - 300]
        if len(attempts) >= 10:
            raise DomainError("RATE_LIMITED", "登录尝试过于频繁，请稍后重试", 429)
        attempts.append(time.monotonic())
        token, csrf, user = app.state.platform.login(body.username, body.password)
        attempts.clear()
        response.set_cookie(
            "office_session",
            token,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
            max_age=43200,
        )
        return dict(
            schema_version="1.0", user=user, csrf_token=csrf, initialized=True, limits=limits()
        )

    @app.post("/api/v1/auth/logout")
    async def logout(request: Request, response: Response):
        auth_write(request)
        app.state.platform.logout(request.cookies.get("office_session", ""))
        response.delete_cookie("office_session")
        return {"ok": True}

    @app.post("/api/v1/auth/password")
    async def password(
        body: PasswordChange, request: Request, response: Response, user=Depends(identity)
    ):
        auth_write(request)
        app.state.platform.change_password(user, body.current_password, body.new_password)
        response.delete_cookie("office_session")
        return {"ok": True}

    @app.get("/api/v1/admin/users", response_model=list[UserInfo])
    async def list_users(user=Depends(identity)):
        admin(user)
        with app.state.db.engine.connect() as c:
            return [
                user_public(r)
                for r in c.execute(select(users).order_by(users.c.created_at)).mappings()
            ]

    @app.patch("/api/v1/admin/users/{user_id}", response_model=UserInfo)
    async def change_user(user_id: str, body: UserUpdate, auth=Depends(writer)):
        admin(auth[0])
        if user_id == auth[0] and body.password is not None:
            raise DomainError("SELF_PASSWORD_RESET", "请在账号设置中修改自己的密码", 409)
        return app.state.platform.update_user(user_id, **body.model_dump(exclude_none=True))

    @app.get("/api/v1/skills", response_model=ResourceList)
    async def list_skills(user=Depends(identity)):
        return dict(schema_version="1.0", items=app.state.office.resources.list("skill", user))

    @app.post("/api/v1/skills", response_model=ResourceInfo, status_code=201)
    async def create_skill(body: SkillWrite, auth=Depends(writer)):
        # Resource writes use a single store transaction; replay maps to the original resource ID.
        return resource_create("skill", body, auth)

    @app.get("/api/v1/agents", response_model=ResourceList)
    async def list_agents(user=Depends(identity)):
        return dict(schema_version="1.0", items=app.state.office.resources.list("agent", user))

    @app.post("/api/v1/agents", response_model=ResourceInfo, status_code=201)
    async def create_agent(body: AgentWrite, auth=Depends(writer)):
        return resource_create("agent", body, auth)

    def resource_create(kind, body, auth):
        user, key = auth
        resource_id = str(
            __import__("uuid").UUID(hashlib.sha256((user + kind + key).encode()).hexdigest()[:32])
        )
        service = app.state.office.resources
        content = body.model_dump(exclude={"visibility", "publish"})
        if service.exists(resource_id):
            old = service.get(resource_id, user)
            # Normalization may add required tool dependencies.
            with app.state.db.engine.connect() as c:
                content = service.normalize(kind, content, user, c)
            if old.content.model_dump() != content or (
                body.visibility is not None and body.visibility != old.visibility
            ):
                raise DomainError("IDEMPOTENCY_CONFLICT", status=409)
            return old
        return service.create(
            kind,
            user,
            content,
            resource_id,
            visibility=body.visibility or "private",
            publish=body.publish,
        )

    # Separate typed handlers keep generated frontend contracts accurate.
    @app.put("/api/v1/skills/{resource_id}", response_model=ResourceInfo)
    async def update_skill(resource_id: str, body: SkillWrite, auth=Depends(writer)):
        if app.state.office.resources.get(resource_id, auth[0]).kind != "skill":
            raise DomainError("NOT_FOUND", status=404)
        return app.state.office.resources.update(
            resource_id,
            auth[0],
            body.model_dump(exclude={"visibility", "publish"}),
            body.visibility,
            body.publish,
            auth[1],
        )

    @app.put("/api/v1/agents/{resource_id}", response_model=ResourceInfo)
    async def update_agent(resource_id: str, body: AgentWrite, auth=Depends(writer)):
        if app.state.office.resources.get(resource_id, auth[0]).kind != "agent":
            raise DomainError("NOT_FOUND", status=404)
        return app.state.office.resources.update(
            resource_id,
            auth[0],
            body.model_dump(exclude={"visibility", "publish"}),
            body.visibility,
            body.publish,
            auth[1],
        )

    def delete_resource(kind, resource_id, auth):
        if app.state.office.resources.get(resource_id, auth[0]).kind != kind:
            raise DomainError("NOT_FOUND", status=404)
        app.state.office.resources.delete(resource_id, auth[0])
        return {"deleted": resource_id}

    @app.delete("/api/v1/skills/{resource_id}", response_model=dict[str, str])
    async def delete_skill(resource_id: str, auth=Depends(writer)):
        return delete_resource("skill", resource_id, auth)

    @app.delete("/api/v1/agents/{resource_id}", response_model=dict[str, str])
    async def delete_agent(resource_id: str, auth=Depends(writer)):
        return delete_resource("agent", resource_id, auth)

    @app.get("/api/v1/skills/{resource_id}", response_model=ResourceInfo)
    @app.get("/api/v1/agents/{resource_id}", response_model=ResourceInfo)
    async def resource_detail(resource_id: str, user=Depends(identity)):
        return app.state.office.resources.get(resource_id, user)

    @app.post("/api/v1/skills/{resource_id}/publish", response_model=ResourceInfo)
    @app.post("/api/v1/agents/{resource_id}/publish", response_model=ResourceInfo)
    async def publish_resource(resource_id: str, auth=Depends(writer)):
        user, key = auth
        # A deterministic per-request release marker prevents duplicate publish clicks.
        from packages.adapters.storage import idempotency

        marker = hashlib.sha256((user + "/publish/" + resource_id + key).encode()).hexdigest()
        with app.state.db.engine.connect() as c:
            old = c.execute(
                select(idempotency.c.data).where(idempotency.c.key == marker)
            ).scalar_one_or_none()
        if old:
            return app.state.office.resources.get(resource_id, user)
        result = app.state.office.resources.publish(resource_id, user)
        with app.state.db.engine.begin() as c:
            c.execute(
                idempotency.insert().values(
                    key=marker, hash=marker, data={"version": result.published_version}
                )
            )
        return result

    @app.patch("/api/v1/skills/{resource_id}", response_model=ResourceInfo)
    @app.patch("/api/v1/agents/{resource_id}", response_model=ResourceInfo)
    async def resource_state(resource_id: str, body: ResourceState, auth=Depends(writer)):
        return app.state.office.resources.state(
            resource_id, auth[0], **body.model_dump(exclude_none=True)
        )

    @app.get("/api/v1/tools", response_model=ToolList)
    async def list_tools(user=Depends(identity)):
        with app.state.db.engine.connect() as c:
            states = dict(c.execute(select(tool_settings.c.id, tool_settings.c.enabled)).all())
        return dict(
            schema_version="1.0",
            items=[
                ToolInfo(
                    id=key,
                    title=title,
                    description=desc,
                    parameters=model.model_json_schema(),
                    enabled=states.get(key, True),
                )
                for key, (title, desc, model) in TOOL_REGISTRY.items()
            ],
        )

    @app.patch("/api/v1/tools/{tool}", response_model=ToolInfo)
    async def update_tool(tool: str, body: ToolState, auth=Depends(writer)):
        admin(auth[0])
        if tool not in TOOL_REGISTRY:
            raise DomainError("NOT_FOUND", status=404)
        with app.state.db.engine.begin() as c:
            c.execute(tool_settings.delete().where(tool_settings.c.id == tool))
            c.execute(tool_settings.insert().values(id=tool, enabled=body.enabled))
        title, desc, model = TOOL_REGISTRY[tool]
        return ToolInfo(
            id=tool,
            title=title,
            description=desc,
            parameters=model.model_json_schema(),
            enabled=body.enabled,
        )

    @app.post("/api/v1/files", response_model=FileInfo, status_code=201)
    async def upload_file(file: UploadFile, auth=Depends(writer)):
        try:
            return await app.state.office.files.upload(file, auth[0], auth[1])
        finally:
            await file.close()

    @app.get("/api/v1/files/{file_id}", response_model=FileInfo)
    async def file_info(file_id: str, user=Depends(identity)):
        row, _ = app.state.office.files.require(file_id, user)
        return app.state.office.files.info(row)

    @app.get("/api/v1/files/{file_id}/download")
    async def download(file_id: str, user=Depends(identity)):
        row, path = app.state.office.files.require(file_id, user)
        if hashlib.sha256(path.read_bytes()).hexdigest() != row["data"]["sha256"]:
            raise DomainError("FILE_INTEGRITY_ERROR", "文件校验失败", 503)
        return FileResponse(path, filename=row["data"]["name"], media_type=row["data"]["mime"])

    @app.post("/api/v1/conversations", response_model=ConversationInfo, status_code=201)
    async def create_conversation(body: ConversationCreate, auth=Depends(writer)):
        user, key = auth
        snapshot = app.state.office.resources.snapshot(body.agent_id, body.version, user)
        if "skills" not in snapshot["content"]:
            raise DomainError("INVALID_AGENT", status=422)

        def action(c):
            d = dict(
                id=uid(),
                title="新任务",
                agent_id=body.agent_id,
                agent_version=snapshot["version"],
                agent_title=snapshot["content"]["title"],
                greeting=snapshot["content"].get("greeting", ""),
                examples=snapshot["content"].get("examples", []),
                created_at=now(),
            )
            c.execute(conversations.insert().values(id=d["id"], owner_id=user, data=d))
            return d

        return write_idempotent(app.state.db, user, "office/conversations", key, body, action)

    @app.get("/api/v1/conversations", response_model=ConversationList)
    async def list_conversations(user=Depends(identity)):
        with app.state.db.engine.connect() as c:
            rows = [
                r
                for r in c.execute(
                    select(conversations.c.data).where(conversations.c.owner_id == user)
                ).scalars()
            ]
        return dict(
            schema_version="1.0",
            items=sorted(rows, key=lambda r: r["created_at"], reverse=True)[:200],
        )

    @app.patch("/api/v1/conversations/{conversation_id}", response_model=ConversationInfo)
    async def rename_conversation(
        conversation_id: str, body: ConversationRename, auth=Depends(writer)
    ):
        with app.state.db.engine.begin() as c:
            d = app.state.office.conversation(conversation_id, auth[0], c)
            d["title"] = body.title
            c.execute(
                conversations.update().where(conversations.c.id == conversation_id).values(data=d)
            )
        return d

    @app.delete("/api/v1/conversations/{conversation_id}")
    async def delete_conversation(conversation_id: str, auth=Depends(writer)):
        with app.state.db.engine.begin() as c:
            app.state.office.conversation(conversation_id, auth[0], c)
            active = (
                c.execute(
                    select(executions.c.id).where(
                        executions.c.owner_id == auth[0],
                        executions.c.status.in_(["queued", "running"]),
                    )
                )
                .scalars()
                .all()
            )
            for execution_id in active:
                if (
                    app.state.office.get_row(execution_id)["data"].get("conversation_id")
                    == conversation_id
                ):
                    raise DomainError("TASK_BUSY", "请先取消正在执行的任务", 409)
            c.execute(messages.delete().where(messages.c.conversation_id == conversation_id))
            c.execute(conversations.delete().where(conversations.c.id == conversation_id))
        return {"ok": True}

    @app.get("/api/v1/conversations/{conversation_id}/messages", response_model=OfficeMessages)
    async def list_messages(conversation_id: str, user=Depends(identity)):
        with app.state.db.engine.connect() as c:
            app.state.office.conversation(conversation_id, user, c)
            rows = (
                c.execute(
                    select(messages.c.data).where(messages.c.conversation_id == conversation_id)
                )
                .scalars()
                .all()
            )
        return dict(
            schema_version="1.0",
            items=sorted(rows, key=lambda m: (m["created_at"], m["id"]))[-200:],
        )

    @app.post(
        "/api/v1/conversations/{conversation_id}/messages",
        response_model=ExecutionInfo,
        status_code=202,
    )
    async def send_message(conversation_id: str, body: MessageCreate, auth=Depends(writer)):
        user, key = auth
        if len(body.attachment_ids) > limits()["attachments"]:
            raise DomainError("TOO_MANY_ATTACHMENTS", "超过本次消息的附件数量上限", 422)
        if not app.state.client.ready:
            raise DomainError("DEPENDENCY_NOT_READY", "模型未就绪，工具仍可直接使用", 503)
        with app.state.db.engine.connect() as c:
            conversation = app.state.office.conversation(conversation_id, user, c)
        app.state.office.resources.snapshot(
            conversation["agent_id"], conversation["agent_version"], user
        )
        attachments = [
            app.state.office.files.info(app.state.office.files.require(i, user)[0]).model_dump()
            for i in body.attachment_ids
        ]

        def action(c):
            for r in c.execute(
                select(executions).where(
                    executions.c.owner_id == user, executions.c.status.in_(["queued", "running"])
                )
            ).mappings():
                if r["data"].get("conversation_id") == conversation_id:
                    raise DomainError("TASK_BUSY", "本任务有请求正在处理", 409)
            execution_id = app.state.office.create(
                user,
                "agent",
                dict(agent_id=conversation["agent_id"], version=conversation["agent_version"]),
                conversation_id,
                c,
            )
            message = dict(
                id=uid(),
                role="user",
                content=body.content,
                files=attachments,
                execution_id=execution_id,
                created_at=now(),
            )
            c.execute(
                messages.insert().values(
                    id=message["id"], conversation_id=conversation_id, data=message
                )
            )
            for file_id in body.attachment_ids:
                c.execute(
                    files.update()
                    .where(files.c.id == file_id, files.c.owner_id == user)
                    .values(conversation_id=conversation_id)
                )
            if conversation["title"] == "新任务":
                c.execute(
                    conversations.update()
                    .where(conversations.c.id == conversation_id)
                    .values(data={**conversation, "title": body.content[:40]})
                )
            return {"id": execution_id}

        result = write_idempotent(
            app.state.db, user, "office/messages/" + conversation_id, key, body, action
        )
        app.state.office.wake.set()
        return app.state.office.info(result["id"], user)

    @app.post("/api/v1/tool-executions", response_model=ExecutionInfo, status_code=202)
    async def run_tool(body: ToolRun, auth=Depends(writer)):
        user, key = auth
        arguments = validate_arguments(body.tool, body.arguments)
        for name, file_id in arguments.items():
            if name.endswith("file_id") and file_id:
                app.state.office.files.require(file_id, user)

        def action(c):
            execution_id = app.state.office.create(
                user, "tool", dict(tool=body.tool, arguments=arguments), None, c
            )
            return {"id": execution_id}

        result = write_idempotent(app.state.db, user, "office/tool", key, body, action)
        app.state.office.wake.set()
        return app.state.office.info(result["id"], user)

    @app.get("/api/v1/executions/{execution_id}", response_model=ExecutionInfo)
    async def execution_info(execution_id: str, user=Depends(identity)):
        return app.state.office.info(execution_id, user)

    @app.get("/api/v1/executions", response_model=list[ExecutionInfo])
    async def list_executions(user=Depends(identity)):
        with app.state.db.engine.connect() as c:
            rows = (
                c.execute(select(executions.c.id).where(executions.c.owner_id == user))
                .scalars()
                .all()
            )
        return sorted(
            [app.state.office.info(i, user) for i in rows], key=lambda r: r.created_at, reverse=True
        )[:200]

    @app.post("/api/v1/executions/{execution_id}/cancel", response_model=ExecutionInfo)
    async def cancel_execution(execution_id: str, auth=Depends(writer)):
        return await app.state.office.cancel(execution_id, auth[0])

    @app.post("/internal/v1/office/{tool}")
    async def internal_tool(tool: str, body: InternalOfficeCall, request: Request):
        expected = app.state.platform_service_token
        if not expected or not hmac.compare_digest(
            request.headers.get("x-mcp-service-token", ""), expected
        ):
            raise DomainError("TOOL_FORBIDDEN", status=403)
        return await app.state.office.call(body.context_token, tool, body.arguments)
