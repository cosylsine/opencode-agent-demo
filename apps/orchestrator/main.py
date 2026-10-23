import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from alembic.migration import MigrationContext
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import String, select
from starlette.exceptions import HTTPException

from apps.orchestrator.config import Settings
from apps.orchestrator.engine import Engine, make_run
from apps.orchestrator.office import OfficeEngine
from apps.orchestrator.office_api import install_routes
from apps.orchestrator.st_code import STEngine, install_st_routes
from apps.orchestrator.tuning import TERMINAL as TUNING_TERMINAL
from apps.orchestrator.tuning import TuningEngine, new_run
from packages.adapters.catalog import (
    read_catalog,
)
from packages.adapters.example import ExampleEvaluator, ExampleObservationSource
from packages.adapters.live_telemetry import LiveTelemetry
from packages.adapters.opencode import OpenCodeClient
from packages.adapters.platform_store import Platform, owners
from packages.adapters.storage import (
    SeriesStore,
    Store,
    chat_messages,
    chat_sessions,
    events,
    idempotency,
    runs,
    triggers,
)
from packages.adapters.storage import settings as settings_table
from packages.contracts.api import (
    AvailableModel,
    AvailableModels,
    CatalogResponse,
    ChatMessage,
    ChatMessages,
    ChatRequest,
    ChatSession,
    ChatSessionRename,
    ChatSessions,
    ChatTurn,
    CsrfResponse,
    EmptyRequest,
    ErrorResponse,
    EventsResponse,
    HealthResponse,
    IterationsResponse,
    LiveTelemetryStatus,
    LoopStatusResponse,
    ReviewRequest,
    RunDetail,
    RunList,
    Scenario,
    Scenarios,
    SchedulerConfig,
    SeriesResponse,
    TriggerRequest,
    TriggerResponse,
)
from packages.contracts.common import ComponentHealth, Envelope, ErrorDetail, SourceKind, now, uid
from packages.contracts.tuning import TuningRun, TuningRuns, TuningStart, TuningTrace
from packages.domain.authorization import Tokens
from packages.domain.core import SCENARIOS, DomainError, assess, digest, metrics, quality
from packages.domain.states import TERMINAL

logger = logging.getLogger("demo")


def requests_check(message: str) -> bool:
    """Route only explicit actions; informational questions remain conversation."""
    clean = message.strip().lower()
    if re.search(r"(?:不用|无需|不要|不必|别)\s*(?:再|进行|发起)?\s*(?:检查|检测|诊断|排查|评估|分析)", clean):
        return False
    if re.search(r"如何|怎么|为什么|什么是|含义|解释|是否|能否|可以.*吗", clean) and not re.search(
        r"请|帮我|现在|开始|发起", clean
    ):
        return False
    if re.search(r"(?:定义|原理|概念|作用|区别)", clean) and not re.search(r"状态|压力|回路|工况", clean):
        return False
    action = re.search(r"检查|检测|诊断|排查|评估|分析", clean)
    target = re.search(r"pid|压力|状态|系统|回路|参数|工况", clean)
    direct = re.match(
        r"^(请|帮我|现在|开始|做|进行|发起|启动|再|重新|来)?\s*(做|进行|发起|启动)?\s*(一[次遍]?)?\s*(检查|检测|诊断)",
        clean,
    )
    return bool(action and (target or direct))


class ToolRequest(Envelope):
    call_context_token: str


def public(run):
    return RunDetail.model_validate_json(run.model_dump_json(include=set(RunDetail.model_fields)))


def create_app(
    config: Settings | None = None,
    client_factory=None,
    source_factory=None,
    evaluator_factory=None,
    worker_enabled=True,
):
    config = config or Settings.from_env()
    config.validate()

    lan_networks = tuple(
        ipaddress.ip_network(value.strip())
        for value in os.environ.get("DEMO_ALLOWED_CIDRS", "").split(",")
        if value.strip()
    )
    loopback_peers = ("127.0.0.1", "::1", "testclient")

    def lan_peer_allowed(host):
        if host in loopback_peers:
            return True
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return False
        return any(address in network for network in lan_networks)

    @asynccontextmanager
    async def lifespan(app):
        db = Store(config.state_dir)
        series = SeriesStore(db)
        client = client_factory(config) if client_factory else OpenCodeClient(config)
        tokens = Tokens()
        live = None if source_factory else LiveTelemetry(config.state_dir)
        source = source_factory(series) if source_factory else ExampleObservationSource(series, live)
        evaluator = (
            evaluator_factory(db, series) if evaluator_factory else ExampleEvaluator(db, series)
        )
        engine = Engine(db, series, source, evaluator, client, tokens, config, monitor_only=True)
        tuning = TuningEngine(db, client, tokens, config.catalog_root)
        app.state.db, app.state.series, app.state.client = db, series, client
        app.state.engine, app.state.tokens = engine, tokens
        app.state.tuning = tuning
        app.state.live = live
        app.state.platform = Platform(db, config.catalog_root)
        if app.state.platform.initialized():
            app.state.platform.import_legacy(app.state.platform.admin_id())
        app.state.office = OfficeEngine(app.state.platform, client)
        app.state.st_code = STEngine(db, client, config.model or "unconfigured")
        app.state.platform_service_token = config.service_token
        app.state.auth_rate = {}
        app.state.csrf_secret = secrets.token_bytes(32)
        app.state.rate = {}
        app.state.chat_locks = {}

        async def supervise():
            while True:
                try:
                    await engine.serve()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("worker failure; task persisted")
                    await asyncio.sleep(1)

        async def probe():
            while True:
                await client.probe()
                await asyncio.sleep(60 if client.ready else 5)

        async def scheduler():
            # Current slot only: no replay of ticks missed while stopped.
            while True:
                await asyncio.sleep(1)
                cfg = read_scheduler(db)
                if cfg.enabled and client.ready:
                    slot = int(time.time()) // cfg.interval_seconds
                    try:
                        write_idempotent(
                            db,
                            "scheduler",
                            "tick",
                            str(slot) + ":" + digest(cfg),
                            cfg,
                            lambda conn: trigger(conn, cfg.scenario_id, app.state.platform.admin_id(), scheduler=True),
                        )
                        engine.wake.set()
                    except DomainError as exc:
                        if exc.code != "SCENARIO_BUSY":
                            logger.warning("scheduler: %s", exc.code)

        tasks = [asyncio.create_task(probe())]
        if worker_enabled:
            tasks.extend([asyncio.create_task(supervise()), asyncio.create_task(scheduler()), asyncio.create_task(tuning.serve()), asyncio.create_task(app.state.office.serve()), asyncio.create_task(app.state.st_code.serve())])
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await evaluator.close()
            await client.close()
            db.close()

    app = FastAPI(
        title="OpenCode PID 示例建议 Demo",
        version="1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        responses={
            code: {"model": ErrorResponse}
            for code in (401, 403, 404, 405, 409, 413, 422, 429, 500, 503)
        },
    )

    def error_response(code, message, status):
        return JSONResponse(
            status_code=status,
            content=ErrorResponse(
                schema_version="1.0",
                error=ErrorDetail(
                    code=code, message=message[:2000], retryable=status == 503, trace_id=uid()
                ),
            ).model_dump(mode="json"),
        )

    @app.exception_handler(DomainError)
    async def domain_error(request, exc):
        return error_response(exc.code, exc.message, exc.status)

    @app.exception_handler(RequestValidationError)
    async def invalid(request, exc):
        # Do not echo inputs, which might include secrets or prompt injection.
        return error_response("VALIDATION_ERROR", "请求不符合接口契约", 422)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error_response(
            "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR",
            "请求路径或方法不可用",
            exc.status_code,
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        logger.error("API failure: %s", type(exc).__name__)
        return error_response("INTERNAL_ERROR", "服务内部错误", 500)

    @app.middleware("http")
    async def request_limits(request, call_next):
        peer = request.client.host if request.client else ""
        if request.url.path.startswith("/api/v1/") and not lan_peer_allowed(peer):
            return error_response("UNAUTHENTICATED", "客户端不在允许访问的网段内", 401)
        if request.url.path.startswith("/internal/") and peer not in loopback_peers:
            response = error_response("FORBIDDEN", "内部接口只允许本机访问", 403)
            response.headers["X-OpenCode-Demo"] = "lan-fix-20261014"
            return response
        upload = request.url.path == "/api/v1/files" and request.method == "POST"
        office_json = request.url.path.startswith(("/api/v1/skills", "/api/v1/agents", "/api/v1/conversations", "/api/v1/tool-executions", "/api/v1/st-runs", "/internal/v1/office/"))
        limit = (int(os.getenv("OFFICE_MAX_FILE_BYTES", str(20 * 1024 * 1024))) + 1024 * 1024
                 if upload else 2 * 1024 * 1024 if office_json else 32768)
        length = request.headers.get("content-length", "0")
        try:
            if int(length) > limit:
                return error_response("REQUEST_TOO_LARGE", "请求超过大小上限", 413)
        except ValueError:
            return error_response("VALIDATION_ERROR", "非法长度", 422)
        if upload:
            receive = request._receive
            received = 0
            async def bounded_receive():
                nonlocal received
                message = await receive()
                received += len(message.get("body", b""))
                if received > limit:
                    raise DomainError("REQUEST_TOO_LARGE", "附件超过大小上限", 413)
                return message
            request._receive = bounded_receive
        elif request.method in ("POST", "PUT", "PATCH", "DELETE"):
            body = await request.body()
            if len(body) > limit:
                return error_response("REQUEST_TOO_LARGE", "请求超过大小上限", 413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-OpenCode-Demo"] = "lan-fix-20261014"
        if "lancheck" in request.query_params:
            logger.warning(
                "LAN_CHECK peer=%r host=%r ua=%r status=%s",
                peer, request.headers.get("host", "")[:120],
                request.headers.get("user-agent", "")[:200], response.status_code,
            )
        return response

    async def identity(request: Request):
        host = request.client.host if request.client else ""
        if not lan_peer_allowed(host):
            raise DomainError("UNAUTHENTICATED", "客户端不在允许访问的网段内", 401)
        session = app.state.platform.session(request.cookies.get("office_session"))
        if not session:
            raise DomainError("UNAUTHENTICATED", "请先登录", 401)
        user = session[0]
        if user["must_change_password"] and request.url.path not in (
            "/api/v1/auth/password", "/api/v1/auth/logout", "/api/v1/auth/me", "/api/v1/csrf-token"):
            raise DomainError("PASSWORD_CHANGE_REQUIRED", "请先修改管理员重置的密码", 403)
        path = request.url.path
        if path.startswith(("/api/v1/runs/", "/api/v1/tuning-runs/")):
            app.state.platform.require_owner(path.split("/")[4], user["id"])
        return user["id"]

    def csrf(user):
        return hmac.new(app.state.csrf_secret, user.encode(), "sha256").hexdigest()

    def origin_allowed(origin):
        allowed_origins = {config.allowed_origin}
        if config.local_dev:
            app_port = int(os.environ.get("APP_PORT", "18000"))
            allowed_origins.update({
                f"http://127.0.0.1:{app_port}",
                f"http://localhost:{app_port}",
            })
        return origin in allowed_origins

    app.state.origin_allowed = origin_allowed

    async def writer(request: Request, user=Depends(identity)):
        session = app.state.platform.session(request.cookies.get("office_session"))
        if not origin_allowed(request.headers.get("origin")) or not session or not hmac.compare_digest(
            request.headers.get("x-csrf-token", ""), session[1]
        ):
            raise DomainError("CSRF_REJECTED", status=403)
        key = request.headers.get("idempotency-key", "")
        if not key or len(key) > 128:
            raise DomainError("IDEMPOTENCY_KEY_REQUIRED", status=422)
        return user, key

    def write_idempotent(db, user, path, key, body, action):
        namespace = digest([user, path, key])
        body_hash = digest(body)
        with db.engine.begin() as conn:
            old = (
                conn.execute(select(idempotency).where(idempotency.c.key == namespace))
                .mappings()
                .first()
            )
            if old:
                if old["hash"] != body_hash:
                    raise DomainError("IDEMPOTENCY_CONFLICT")
                return old["data"]
            value = action(conn)
            if isinstance(value, BaseModel):
                value = value.model_dump(mode="json")
            conn.execute(idempotency.insert().values(key=namespace, hash=body_hash, data=value))
            return value

    def trigger(conn, scenario, user, parent=None, scheduler=False):
        if scenario not in SCENARIOS:
            raise DomainError("SCENARIO_NOT_FOUND", status=422)
        db = app.state.db
        active = db.active(conn)
        if not user:
            raise DomainError("SETUP_REQUIRED", "请先初始化管理员", 409)
        if active and not app.state.platform.owns(active[0].run_id, user, conn):
            raise DomainError("SCENARIO_BUSY", "已有检查运行，请稍后重试", 409)
        if active and active[0].scenario_id != scenario:
            raise DomainError("SCENARIO_BUSY")
        if not app.state.client.ready:
            raise DomainError("DEPENDENCY_NOT_READY", "模型未就绪，历史仍可查询", 503)
        if app.state.live and not app.state.live.window_ready(scenario, now()):
            raise DomainError(
                "DEPENDENCY_NOT_READY", "示例回路未运行或尚无连续 120 秒观测", 503
            )
        if not active:
            run = make_run(scenario, config, parent)
            db.add(run, conn)
            app.state.platform.claim(run.run_id, user, conn)
        else:
            run = active[0]
        response = TriggerResponse(
            schema_version="1.0",
            trigger_id=uid(),
            run_id=run.run_id,
            coalesced=bool(active),
            status_url="/api/v1/runs/" + run.run_id,
        )
        conn.execute(
            triggers.insert().values(
                id=response.trigger_id,
                run_id=run.run_id,
                data={**response.model_dump(mode="json"), "actor": user, "created_at": now()},
            )
        )
        return response

    @app.get("/live")
    async def live(request: Request):
        if request.client.host not in ("127.0.0.1", "::1", "testclient"):
            raise DomainError("FORBIDDEN", status=403)
        return {"schema_version": "1.0", "live": True}

    @app.get("/api/v1/csrf-token", response_model=CsrfResponse)
    async def csrf_token(request: Request, user=Depends(identity)):
        return CsrfResponse(schema_version="1.0", token=app.state.platform.session(request.cookies.get("office_session"))[1])

    @app.get("/api/v1/catalog", response_model=CatalogResponse)
    async def catalog(user=Depends(identity)):
        catalog = read_catalog(config.catalog_root)
        return catalog.model_copy(update={"skills": [s for s in catalog.skills if not s.deletable], "deleted_skills": []})

    @app.get("/api/v1/models", response_model=AvailableModels)
    async def available_models(user=Depends(identity)):
        return AvailableModels(
            schema_version="1.0",
            items=[AvailableModel(model_id=config.model, provider=config.provider or "unknown",
                                  ready=app.state.client.ready)] if config.model else [],
        )

    @app.get("/api/v1/scenarios", response_model=Scenarios)
    async def scenarios(user=Depends(identity)):
        return Scenarios(
            schema_version="1.0",
            items=[
                Scenario(
                    scenario_id=k,
                    name=v,
                    description=v + "；确定性示例程序",
                    source_kind=SourceKind.EXAMPLE,
                )
                for k, v in SCENARIOS.items()
            ],
        )

    @app.post("/api/v1/triggers", response_model=TriggerResponse, status_code=202)
    async def create_trigger(body: TriggerRequest, auth=Depends(writer)):
        user, key = auth
        recent = app.state.rate.setdefault(user, [])
        recent[:] = [t for t in recent if time.monotonic() - t < 60]
        if len(recent) >= 30:
            raise DomainError("RATE_LIMITED", status=429)
        recent.append(time.monotonic())
        result = write_idempotent(
            app.state.db,
            user,
            "trigger",
            key,
            body,
            lambda conn: trigger(conn, body.scenario_id, user),
        )
        app.state.engine.wake.set()
        return result

    @app.post("/api/v1/tuning-runs", response_model=TuningRun, status_code=202)
    async def start_tuning(body: TuningStart, auth=Depends(writer)):
        user, key = auth
        if not app.state.client.ready:
            raise DomainError("DEPENDENCY_NOT_READY", "Control Agent 不可用", 503)

        def action(conn):
            run = new_run(body.plant_state)
            app.state.db.add_tuning(run, conn)
            app.state.platform.claim(run.run_id, user, conn)
            app.state.db.audit(run.run_id, "TUNING_STARTED", {
                "actor": user, "plant_state_hash": run.plant_state_hash,
                "plant_state": run.plant_state.model_dump(mode="json"),
            }, conn)
            return run

        value = write_idempotent(app.state.db, user, "tuning-runs", key, body, action)
        app.state.tuning.wake.set()
        return TuningRun.model_validate_json(json.dumps(value))

    @app.get("/api/v1/tuning-runs", response_model=TuningRuns)
    async def list_tuning(user=Depends(identity)):
        return TuningRuns(schema_version="1.0", items=app.state.db.list_tuning(user))

    @app.get("/api/v1/tuning-runs/{run_id}", response_model=TuningRun)
    async def tuning_detail(run_id: str, user=Depends(identity)):
        return app.state.db.get_tuning(run_id)

    @app.get("/api/v1/tuning-runs/{run_id}/events", response_model=EventsResponse)
    async def tuning_events(run_id: str, user=Depends(identity)):
        app.state.db.get_tuning(run_id)
        with app.state.db.engine.connect() as conn:
            records = conn.execute(select(events).where(events.c.run_id == run_id).order_by(events.c.id).limit(200)).mappings().all()
        return {"schema_version": "1.0", "items": [
            {"event_id": r["id"], "run_id": r["run_id"], "created_at": r["created_at"],
             "event": r["event"], "detail": r["detail"]} for r in records
        ], "next_event_id": None}

    @app.get("/api/v1/tuning-runs/{run_id}/trials/{trial_id}/scenarios/{scenario_id}/trace", response_model=TuningTrace)
    async def tuning_trace(run_id: str, trial_id: str, scenario_id: str, user=Depends(identity)):
        from uuid import UUID

        try:
            UUID(run_id)
            UUID(trial_id)
        except ValueError:
            raise DomainError("NOT_FOUND", status=404) from None
        run = app.state.db.get_tuning(run_id)
        trial = next((item for item in run.trials if item.trial_id == trial_id), None)
        if trial is None or scenario_id not in trial.traces:
            raise DomainError("NOT_FOUND", status=404)
        path = app.state.tuning.trace_path(run_id, trial_id, scenario_id)
        if not path.is_file():
            raise DomainError("RESULT_UNKNOWN", status=503)
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != trial.trace_hashes.get(scenario_id):
            raise DomainError("RESULT_INTEGRITY_ERROR", status=503)
        return TuningTrace(schema_version="1.0", run_id=run_id, trial_id=trial_id,
                           scenario_id=scenario_id, source_kind=SourceKind.SIMULATION,
                           points=json.loads(raw))

    @app.post("/api/v1/tuning-runs/{run_id}/cancel", response_model=TuningRun, status_code=202)
    async def cancel_tuning(run_id: str, body: EmptyRequest, auth=Depends(writer)):
        user, key = auth

        def action(conn):
            run = app.state.db.get_tuning(run_id, conn)
            if run.status not in TUNING_TERMINAL and not run.cancel_requested:
                run.cancel_requested = True
                app.state.db.save_tuning(run, conn)
                app.state.db.audit(run_id, "TUNING_CANCEL_REQUESTED", {"actor": user}, conn)
            return run

        value = write_idempotent(app.state.db, user, run_id + "/tuning-cancel", key, body, action)
        app.state.tuning.wake.set()
        if value.get("session_id") and value.get("status") == "AGENT_RUNNING":
            await app.state.client.abort(value["session_id"])
        return TuningRun.model_validate_json(json.dumps(value))

    def owned_session(session_id, user, conn):
        row = (
            conn.execute(
                select(chat_sessions).where(
                    chat_sessions.c.id == session_id, chat_sessions.c.user_id == user
                )
            )
            .mappings()
            .first()
        )
        if not row:
            raise DomainError("NOT_FOUND", "会话不存在", 404)
        return row

    def chat_message(row):
        return ChatMessage(
            message_id=row["id"],
            session_id=row["session_id"],
            role=row["role"],
            content=row["content"],
            created_at=row["created_at"],
            run_id=row["run_id"],
            status=row["status"],
        )

    @app.post("/api/v1/chat/sessions", response_model=ChatSession, status_code=201)
    async def new_chat(body: EmptyRequest, auth=Depends(writer)):
        user, key = auth

        def create(conn):
            session = ChatSession(
                schema_version="1.0", session_id=uid(), title="新对话", created_at=now()
            )
            conn.execute(
                chat_sessions.insert().values(
                    id=session.session_id,
                    user_id=user,
                    title=session.title,
                    created_at=session.created_at,
                )
            )
            return session

        return write_idempotent(app.state.db, user, "chat/sessions", key, body, create)

    @app.get("/api/v1/chat/sessions", response_model=ChatSessions)
    async def list_chat(user=Depends(identity)):
        with app.state.db.engine.connect() as conn:
            rows = (
                conn.execute(
                    select(chat_sessions)
                    .where(chat_sessions.c.user_id == user)
                    .order_by(chat_sessions.c.created_at.desc())
                    .limit(100)
                )
                .mappings()
                .all()
            )
        return ChatSessions(
            schema_version="1.0",
            items=[
                ChatSession(
                    schema_version="1.0",
                    session_id=r["id"],
                    title=r["title"],
                    created_at=r["created_at"],
                )
                for r in rows
            ],
        )

    @app.patch("/api/v1/chat/sessions/{session_id}", response_model=ChatSession)
    async def rename_chat(session_id: str, body: ChatSessionRename, auth=Depends(writer)):
        user, _ = auth
        title = body.title.strip()
        if not title:
            raise DomainError("VALIDATION_ERROR", "会话名称不能为空", 422)
        lock = app.state.chat_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            with app.state.db.engine.begin() as conn:
                session = owned_session(session_id, user, conn)
                conn.execute(chat_sessions.update().where(chat_sessions.c.id == session_id).values(title=title))
        return ChatSession(schema_version="1.0", session_id=session_id, title=title, created_at=session["created_at"])

    @app.delete("/api/v1/chat/sessions/{session_id}", response_model=ChatSession)
    async def delete_chat(session_id: str, auth=Depends(writer)):
        user, _ = auth
        lock = app.state.chat_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            with app.state.db.engine.begin() as conn:
                session = owned_session(session_id, user, conn)
                conn.execute(chat_messages.delete().where(chat_messages.c.session_id == session_id))
                conn.execute(chat_sessions.delete().where(chat_sessions.c.id == session_id))
        app.state.chat_locks.pop(session_id, None)
        return ChatSession(schema_version="1.0", session_id=session_id, title=session["title"], created_at=session["created_at"])

    @app.get("/api/v1/chat/sessions/{session_id}/messages", response_model=ChatMessages)
    async def list_chat_messages(session_id: str, user=Depends(identity)):
        with app.state.db.engine.connect() as conn:
            owned_session(session_id, user, conn)
            rows = (
                conn.execute(
                    select(chat_messages)
                    .where(chat_messages.c.session_id == session_id)
                    .order_by(chat_messages.c.created_at.desc(), chat_messages.c.id.desc())
                    .limit(200)
                )
                .mappings()
                .all()
            )
        return ChatMessages(schema_version="1.0", items=[chat_message(r) for r in reversed(rows)])

    @app.post("/api/v1/chat/sessions/{session_id}/messages", response_model=ChatTurn)
    async def send_chat(session_id: str, body: ChatRequest, auth=Depends(writer)):
        user, key = auth
        content = body.message.strip()
        if not content:
            raise DomainError("VALIDATION_ERROR", "消息不能为空", 422)
        lock = app.state.chat_locks.setdefault(session_id, asyncio.Lock())
        async with lock:
            with app.state.db.engine.begin() as conn:
                session = owned_session(session_id, user, conn)
                previous = (
                    conn.execute(
                        select(chat_messages)
                        .where(chat_messages.c.session_id == session_id)
                        .order_by(chat_messages.c.created_at, chat_messages.c.id)
                    )
                    .mappings()
                    .all()
                )
                same = next(
                    (r for r in previous if r["role"] == "user" and r["request_key"] == key), None
                )
                if same:
                    if same["request_hash"] != digest(body):
                        raise DomainError("IDEMPOTENCY_CONFLICT")
                    answer = next(
                        (r for r in previous if r["request_key"] == key and r["role"] != "user"),
                        None,
                    )
                    if not answer:
                        raise DomainError("CHAT_PENDING", "此条消息仍在处理", 409)
                    return ChatTurn(
                        schema_version="1.0",
                        user_message=chat_message(same),
                        response=chat_message(answer),
                    )
                check = requests_check(content)
                run_id = None
                if check:
                    response = trigger(conn, body.scenario_id, user)
                    run_id = response.run_id
                stamp = now()
                sent = {
                    "id": uid(),
                    "session_id": session_id,
                    "role": "user",
                    "content": content,
                    "created_at": stamp,
                    "run_id": None,
                    "request_key": key,
                    "request_hash": digest(body),
                    "status": "complete",
                }
                conn.execute(chat_messages.insert().values(**sent))
                if session["title"] == "新对话":
                    conn.execute(
                        chat_sessions.update()
                        .where(chat_sessions.c.id == session_id)
                        .values(title=content.replace("\n", " ")[:40])
                    )
            if check:
                app.state.engine.wake.set()
                answer_text = "已发起检查。Monitor 正在读取示例数据；后续状态和建议会显示在下方。"
                role, status = "system", "complete"
            else:
                try:
                    latest_run_id = next(
                        (r["run_id"] for r in reversed(previous) if r["run_id"]), None
                    )
                    run_context = None
                    if latest_run_id:
                        latest_run = public(app.state.db.get(latest_run_id))
                        run_context = {
                            "run_id": latest_run.run_id,
                            "status": latest_run.status,
                            "actual_pid": latest_run.actual_pid.pid.model_dump()
                            if latest_run.actual_pid
                            else None,
                            "recommended_pid": latest_run.best_pid.model_dump()
                            if latest_run.best_pid
                            else None,
                            "monitor_assessment": latest_run.monitor_output.assessment
                            if latest_run.monitor_output
                            else None,
                            "monitor_reason": latest_run.monitor_output.reason
                            if latest_run.monitor_output
                            else None,
                        }
                    answer_text = await app.state.client.chat(previous[-12:], content, run_context)
                    role, status = "assistant", "complete"
                except Exception as exc:
                    logger.warning("reception reply failed: %s", type(exc).__name__)
                    answer_text = "模型回复失败。请确认服务就绪后重新发送消息。"
                    role, status = "system", "error"
            answered = {
                "id": uid(),
                "session_id": session_id,
                "role": role,
                "content": answer_text,
                "created_at": now(),
                "run_id": run_id,
                "request_key": key,
                "request_hash": None,
                "status": status,
            }
            with app.state.db.engine.begin() as conn:
                conn.execute(chat_messages.insert().values(**answered))
            return ChatTurn(
                schema_version="1.0",
                user_message=chat_message(sent),
                response=chat_message(answered),
            )

    @app.get("/api/v1/runs/{run_id}", response_model=RunDetail)
    async def detail(run_id: str, user=Depends(identity)):
        return public(app.state.db.get(run_id))

    @app.get("/api/v1/runs", response_model=RunList)
    async def history(cursor: str | None = None, limit: int = 20, user=Depends(identity)):
        if not 1 <= limit <= 100:
            raise DomainError("VALIDATION_ERROR", status=422)
        created = runs.c.data["created_at"].as_string()
        stmt = select(runs.c.data).where(runs.c.id.in_(select(owners.c.id).where(owners.c.owner_id == user))).order_by(created.desc(), runs.c.id.desc()).limit(limit + 1)
        if cursor:
            try:
                if len(cursor) > 200:
                    raise ValueError("oversize cursor")
                timestamp, last = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
                if not isinstance(timestamp, str) or not isinstance(last, str):
                    raise ValueError("invalid cursor")
            except Exception as exc:
                raise DomainError("VALIDATION_ERROR", status=422) from exc
            stmt = stmt.where((created < timestamp) | ((created == timestamp) & (runs.c.id < last)))
        with app.state.db.engine.connect() as conn:
            rows = list(conn.execute(stmt).scalars())
        items = [
            RunDetail.model_validate_json(
                json.dumps({k: v for k, v in r.items() if k in RunDetail.model_fields})
            )
            for r in rows[:limit]
        ]
        return RunList(
            schema_version="1.0",
            items=items,
            next_cursor=base64.urlsafe_b64encode(
                json.dumps([items[-1].created_at, items[-1].run_id]).encode()
            ).decode()
            if len(rows) > limit
            else None,
        )

    @app.get("/api/v1/runs/{run_id}/iterations", response_model=IterationsResponse)
    async def iterations(run_id: str, user=Depends(identity)):
        return {"schema_version": "1.0", "items": app.state.db.get(run_id).iterations}

    @app.get("/api/v1/runs/{run_id}/events", response_model=EventsResponse)
    async def audit(run_id: str, after_event_id: int = 0, user=Depends(identity)):
        app.state.db.get(run_id)
        with app.state.db.engine.connect() as conn:
            records = (
                conn.execute(
                    select(events)
                    .where(events.c.run_id == run_id, events.c.id > after_event_id)
                    .order_by(events.c.id)
                    .limit(201)
                )
                .mappings()
                .all()
            )
        items = [
            {
                "event_id": r["id"],
                "run_id": r["run_id"],
                "created_at": r["created_at"],
                "event": r["event"],
                "detail": r["detail"],
            }
            for r in records[:200]
        ]
        return {
            "schema_version": "1.0",
            "items": items,
            "next_event_id": items[-1]["event_id"] if len(records) > 200 else None,
        }

    @app.get("/api/v1/series/{ref}", response_model=SeriesResponse)
    async def series(ref: str, max_points: int = 1000, user=Depends(identity)):
        if not 3 <= max_points <= 1000:
            raise DomainError("VALIDATION_ERROR", status=422)
        with app.state.db.engine.connect() as conn:
            allowed = conn.execute(select(runs.c.id).where(runs.c.id.in_(select(owners.c.id).where(owners.c.owner_id == user)),
                runs.c.data.cast(String).contains(ref))).first()
        if not allowed:
            raise DomainError("NOT_FOUND", status=404)
        artifact = app.state.series.resolve(ref)
        points = app.state.series.read(artifact)
        count = len(points)
        if count > max_points:
            # Preserve extrema per bucket and endpoints; never interpolate data.
            inner = points[1:-1]
            bucket_count = max(1, (max_points - 2) // 2)
            chosen = [points[0]]
            for i in range(bucket_count):
                bucket = inner[
                    i * len(inner) // bucket_count : (i + 1) * len(inner) // bucket_count
                ]
                if bucket:
                    extremes = {
                        p.elapsed_seconds: p
                        for p in (
                            min(bucket, key=lambda p: p.pressure or 0),
                            max(bucket, key=lambda p: p.pressure or 0),
                        )
                    }
                    chosen.extend(extremes[k] for k in sorted(extremes))
            points = chosen[: max_points - 1] + [points[-1]]
        return SeriesResponse(
            schema_version="1.0",
            series_ref=ref,
            source_kind=SourceKind.EXAMPLE,
            pressure_unit="demo_unit",
            original_sample_count=count,
            returned_sample_count=len(points),
            downsampled=len(points) < count,
            points=points,
        )

    @app.post("/api/v1/runs/{run_id}/cancel", response_model=RunDetail, status_code=202)
    async def cancel(run_id: str, body: EmptyRequest, auth=Depends(writer)):
        user, key = auth
        result = write_idempotent(
            app.state.db,
            user,
            run_id + "/cancel",
            key,
            body,
            lambda conn: public(app.state.db.cancel_mark(run_id, conn)),
        )
        app.state.tokens.revoke_run(run_id)
        app.state.engine.wake.set()
        return RunDetail.model_validate_json(json.dumps(result))

    @app.post("/api/v1/runs/{run_id}/retry", response_model=TriggerResponse, status_code=202)
    async def retry(run_id: str, body: EmptyRequest, auth=Depends(writer)):
        user, key = auth

        def action(conn):
            run = app.state.db.get(run_id, conn)
            if run.status not in TERMINAL:
                raise DomainError("RUN_STATE_CONFLICT")
            return trigger(conn, run.scenario_id, user, run_id)

        result = write_idempotent(app.state.db, user, run_id + "/retry", key, body, action)
        app.state.engine.wake.set()
        return result

    @app.post("/api/v1/runs/{run_id}/manual-review", response_model=RunDetail | TriggerResponse)
    async def review(run_id: str, body: ReviewRequest, auth=Depends(writer)):
        user, key = auth

        def action(conn):
            run = app.state.db.get(run_id, conn)
            if run.status != "MANUAL_REVIEW_REQUIRED":
                raise DomainError("RUN_STATE_CONFLICT")
            app.state.db.audit(
                run_id,
                "MANUAL_REVIEW",
                {"actor": user, "action": body.action, "comment": body.comment},
                conn,
            )
            return (
                trigger(conn, run.scenario_id, user, run_id)
                if body.action == "REQUEST_RECHECK"
                else public(run)
            )

        result = write_idempotent(app.state.db, user, run_id + "/review", key, body, action)
        app.state.engine.wake.set()
        model = TriggerResponse if "trigger_id" in result else RunDetail
        return model.model_validate_json(json.dumps(result))

    @app.get("/api/v1/control-loops/{loop_id}/status", response_model=LoopStatusResponse)
    async def loop_status(loop_id: str, user=Depends(identity)):
        if loop_id != "feedwater-pump-demo":
            raise DomainError("NOT_FOUND", status=404)
        active = app.state.db.active()
        return {
            "schema_version": "1.0",
            "control_loop_id": loop_id,
            "active_run_id": active[0].run_id if active and app.state.platform.owns(active[0].run_id, user) else None,
        }

    @app.get("/api/v1/telemetry/{scenario_id}/latest", response_model=LiveTelemetryStatus)
    async def latest_telemetry(scenario_id: str, user=Depends(identity)):
        live = app.state.live
        if live is None:
            raise DomainError("NOT_FOUND", status=404)
        row = live.latest(scenario_id)
        return LiveTelemetryStatus(
            schema_version="1.0", scenario_id=scenario_id, running=live.is_running(),
            latest_sample_at=row["timestamp"] if row else None,
            pressure=row["pressure"] if row else None,
            target_pressure=30.0, sample_kind=row["sample_kind"] if row else None,
            jsonl_path=f"telemetry/{scenario_id}.jsonl",
            window_ready=live.window_ready(scenario_id, now()),
        )

    @app.get("/api/v1/scheduler", response_model=SchedulerConfig)
    async def get_scheduler(user=Depends(identity)):
        return read_scheduler(app.state.db)

    @app.put("/api/v1/scheduler", response_model=SchedulerConfig)
    async def set_scheduler(body: SchedulerConfig, auth=Depends(writer)):
        user, key = auth
        if not app.state.platform.get_user(user)["is_admin"]:
            raise DomainError("FORBIDDEN", "定时检查配置需要管理员权限", 403)
        if body.scenario_id not in SCENARIOS:
            raise DomainError("SCENARIO_NOT_FOUND", status=422)

        def action(conn):
            conn.execute(settings_table.delete().where(settings_table.c.key == "scheduler"))
            conn.execute(
                settings_table.insert().values(key="scheduler", data=body.model_dump(mode="json"))
            )
            app.state.db.audit(
                "00000000-0000-0000-0000-000000000000",
                "SCHEDULER_UPDATED",
                {"actor": user, "enabled": body.enabled, "interval_seconds": body.interval_seconds},
                conn,
            )
            return body

        return write_idempotent(app.state.db, user, "scheduler", key, body, action)

    @app.get("/api/v1/health", response_model=HealthResponse)
    async def health():
        db_ready = True
        disk_ready = True
        db_version = None
        try:
            with app.state.db.engine.connect() as conn:
                conn.execute(select(runs.c.id).limit(1))
                db_version = MigrationContext.configure(conn).get_current_revision()
            probe_path = app.state.db.root / "write-probe"
            probe_path.write_text("ok")
            probe_path.unlink()
        except Exception:
            db_ready = False
            disk_ready = False
        client = app.state.client
        source_health = await app.state.engine.source.health()
        ready = client.ready and db_ready and disk_ready and source_health.ready
        return HealthResponse(
            schema_version="1.0",
            live=True,
            ready=ready,
            degraded=not ready,
            components=[
                ComponentHealth(
                    name="database",
                    ready=db_ready,
                    version=db_version,
                    error_code=None if db_ready else "DB_ERROR",
                ),
                ComponentHealth(
                    name="disk",
                    ready=disk_ready,
                    version=None,
                    error_code=None if disk_ready else "DISK_ERROR",
                ),
                ComponentHealth(
                    name="opencode/model",
                    ready=client.ready,
                    version=client.version,
                    error_code=client.error_code,
                ),
                source_health,
            ],
        )

    @app.post("/internal/v1/tools/{tool}")
    async def tool_read(tool: str, body: ToolRequest, request: Request):
        service = request.headers.get("x-mcp-service-token", "")
        if not config.service_token or not hmac.compare_digest(service, config.service_token):
            raise DomainError("UNAUTHENTICATED", status=401)
        scope = app.state.tokens.authorize(body.call_context_token, tool)
        if scope.role == "control" and tool == "get_tuning_context":
            try:
                app.state.db.get_tuning(scope.run_id)
            except DomainError as exc:
                if exc.code != "NOT_FOUND":
                    raise
            else:
                return app.state.tuning.read_context(scope)
        run = app.state.db.get(scope.run_id)
        if (
            run.status in TERMINAL
            or run.cancel_requested
            or (run.deadline and now() >= run.deadline)
        ):
            raise DomainError("TOOL_FORBIDDEN", status=403)
        if scope.role == "control" and (
            not run.iterations or run.iterations[-1].iteration_id != scope.iteration_id
        ):
            raise DomainError("TOOL_FORBIDDEN", status=403)
        if tool == "get_recent_window":
            points = app.state.series.read(run.snapshot.series)
            result = {
                "snapshot_id": run.snapshot.snapshot_id,
                "series": run.snapshot.series.model_dump(mode="json"),
                "points": [point.model_dump(mode="json") for point in points],
                "actual_pid": run.actual_pid.model_dump(mode="json"),
                "quality": quality(points).model_dump(),
                "source_kind": "EXAMPLE",
            }
        elif tool in ("calculate_metrics", "detect_state"):
            m = metrics(app.state.series.read(run.snapshot.series))
            result = (
                m.model_dump(mode="json")
                if tool == "calculate_metrics"
                else assess(m).model_dump(mode="json")
            )
        elif tool == "get_tuning_context":
            payload = app.state.engine.agent_payload(
                run, "control", scope.request_id, "[REDACTED TOKEN]"
            )
            result = payload.model_dump(mode="json", exclude={"call_context_token"})
        else:
            raise DomainError("TOOL_FORBIDDEN", status=403)
        app.state.db.audit(
            run.run_id,
            "MCP_READ",
            {"request_id": scope.request_id, "tool": tool, "role": scope.role},
        )
        return {"schema_version": "1.0", "data": result}

    dist = Path(__file__).parents[1] / "dashboard/dist"

    @app.get("/")
    async def index():
        if (dist / "index.html").exists():
            return FileResponse(dist / "index.html")
        return error_response("UI_NOT_BUILT", "请先构建 dashboard", 503)

    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
    install_routes(app, identity, writer, write_idempotent, lan_peer_allowed)
    install_st_routes(app, identity, writer, write_idempotent)

    return app


def read_scheduler(db):
    with db.engine.connect() as conn:
        data = conn.execute(
            select(settings_table.c.data).where(settings_table.c.key == "scheduler")
        ).scalar_one_or_none()
    return (
        SchedulerConfig.model_validate_json(json.dumps(data))
        if data
        else SchedulerConfig(
            schema_version="1.0", enabled=False, interval_seconds=300, scenario_id="improvable"
        )
    )


app = create_app()
