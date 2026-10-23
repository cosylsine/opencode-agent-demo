"""Fixed ST specialist workflow, account-scoped persistence and reviewable exports."""

import asyncio
import hashlib
import logging
import os

from fastapi import Depends
from fastapi.responses import Response
from pydantic import ValidationError
from sqlalchemy import select

from packages.adapters.st_code import exports, validate_result
from packages.adapters.storage import st_runs
from packages.contracts.common import now, uid
from packages.contracts.st_code import STArtifact, STResult, STRun, STRuns, STRunSummary, STStart
from packages.domain.core import DomainError

logger = logging.getLogger(__name__)
TERMINAL = {"completed", "failed", "cancelled", "interrupted"}


class STEngine:
    def __init__(self, db, client, model_id):
        self.db, self.client, self.model_id = db, client, model_id
        self.model_timeout = max(60, min(1800, int(os.getenv("ST_MODEL_TIMEOUT_SECONDS", "900"))))
        self.wake = asyncio.Event()
        self.active_id, self.active_task = None, None
        self.orphan_sessions = []
        # A restart must never resubmit an inference whose outcome is uncertain.
        with db.engine.begin() as conn:
            rows = (
                conn.execute(select(st_runs.c.data).where(st_runs.c.status == "running"))
                .scalars()
                .all()
            )
            for value in rows:
                run = STRun.model_validate(value)
                if run.session_id:
                    self.orphan_sessions.append(run.session_id)
                run.status, run.error = (
                    "interrupted",
                    "服务重启中断了任务；请复核原始代码后新建任务。",
                )
                self.save(run, conn)

    def save(self, run, conn=None):
        run.updated_at = now()
        if conn is None:
            with self.db.engine.begin() as conn:
                return self.save(run, conn)
        conn.execute(
            st_runs.update()
            .where(st_runs.c.id == run.run_id)
            .values(status=run.status, data=run.model_dump(mode="json"))
        )

    def get(self, run_id, owner=None):
        with self.db.engine.connect() as conn:
            query = select(st_runs.c.data).where(st_runs.c.id == run_id)
            if owner is not None:
                query = query.where(st_runs.c.owner_id == owner)
            value = conn.execute(query).scalar_one_or_none()
        if value is None:
            raise DomainError("NOT_FOUND", "任务不存在", 404)
        return STRun.model_validate(value)

    def create(self, body, owner, conn):
        if not self.client.ready:
            raise DomainError("DEPENDENCY_NOT_READY", "模型未就绪，仍可查看历史结果", 503)
        if conn.execute(
            select(st_runs.c.id).where(
                st_runs.c.owner_id == owner, st_runs.c.status.in_(["queued", "running"])
            )
        ).first():
            raise DomainError("ST_BUSY", "你已有 ST 任务排队或处理中，请等待或取消后再提交", 409)
        stamp = now()
        retry_of = body.retry_from
        if retry_of:
            previous = self.get(retry_of, owner)
            if previous.status not in {"failed", "cancelled", "interrupted"}:
                raise DomainError("ST_RETRY_INVALID", "只能重试已失败、取消或中断的任务", 409)
            if previous.request.model_dump(exclude={"retry_from"}) != body.model_dump(
                exclude={"retry_from"}
            ):
                raise DomainError(
                    "ST_RETRY_INVALID",
                    "重试需保留相同源码、操作和工程说明；修改输入请新建任务",
                    409,
                )
            body = body.model_copy(update={"retry_from": None})
        run = STRun(
            schema_version="1.0",
            run_id=uid(),
            status="queued",
            created_at=stamp,
            updated_at=stamp,
            request=body,
            source_sha256=hashlib.sha256(body.code.encode()).hexdigest(),
            model_id=self.model_id,
            retry_of=retry_of,
        )
        conn.execute(
            st_runs.insert().values(
                id=run.run_id, owner_id=owner, status=run.status, data=run.model_dump(mode="json")
            )
        )
        return run

    def session(
        self,
        run_id,
        session,
        *,
        stage=None,
        attempt=1,
        feedback="",
        request_id=None,
        output=None,
        reused=False,
    ):
        run = self.get(run_id)
        if session:
            run.session_id = session
        if stage:
            run.stage = stage
            if session:
                run.stage_sessions.setdefault(stage, []).append(session)
            if request_id:
                run.stage_requests.setdefault(stage, []).append(request_id)
            if output is not None:
                run.stage_results[stage] = output
            if reused and stage not in run.reused_stages:
                run.reused_stages.append(stage)
            run.attempt_count = max(run.attempt_count, attempt)
        if feedback:
            run.validation_feedback.append(f"{stage}：{feedback}")
        self.save(run)

    async def process(self, run):
        run.status = "running"
        self.save(run)
        try:
            # One bounded correction is permitted only after a known completed,
            # invalid result. Network uncertainty never triggers resubmission.
            correction = ""

            def progress(session, **value):
                self.session(run.run_id, session, **value)

            progress.checkpoint = {}
            if run.retry_of:
                previous = self.get(run.retry_of)
                if (
                    previous.model_id == run.model_id
                    and previous.source_sha256 == run.source_sha256
                ):
                    progress.checkpoint = previous.stage_results.copy()
            for attempt in range(2):
                run = self.get(run.run_id)
                run.attempt_count = attempt + 1
                self.save(run)
                try:
                    result = await asyncio.wait_for(
                        self.client.st_review(
                            run.request,
                            progress,
                            correction,
                        ),
                        self.model_timeout,
                    )
                    result = STResult.model_validate(result)
                    checks = validate_result(run.request, result)
                    break
                except (ValidationError, DomainError) as exc:
                    if (
                        attempt
                        or isinstance(exc, DomainError)
                        and exc.code not in {"ST_OUTPUT_INVALID", "AGENT_OUTPUT_INVALID"}
                    ):
                        raise
                    correction = str(exc)[:1500]
                    run = self.get(run.run_id)
                    run.validation_feedback.append(correction)
                    self.save(run)
            run = self.get(run.run_id)
            if run.status != "running":
                return
            run.result, run.checks, run.status, run.stage = (
                result,
                checks,
                "completed",
                "validation",
            )
            run.artifacts = [
                STArtifact(name=name, download_url=f"/api/v1/st-runs/{run.run_id}/artifacts/{name}")
                for name in exports(run)
            ]
            self.save(run)
        except asyncio.CancelledError:
            run = self.get(run.run_id)
            if run.status == "running":
                run.status, run.error = "interrupted", "服务停止中断任务，请新建任务重试。"
                self.save(run)
            raise
        except Exception as exc:
            logger.warning("ST run %s failed: %s", run.run_id, type(exc).__name__)
            run = self.get(run.run_id)
            if run.status == "running":
                run.status = "failed"
                run.error = (
                    f"{exc.code}：{exc.message}"
                    if isinstance(exc, DomainError)
                    else f"ST_MODEL_TIMEOUT：模型生成超过 {self.model_timeout} 秒，请减少代码长度或所选操作后新建任务。"
                    if isinstance(exc, TimeoutError)
                    else "任务超时或模型结果未通过校验，请缩短代码后新建任务。"
                )[:2000]
                self.save(run)
        finally:
            persisted = self.get(run.run_id)
            if persisted.status != "completed" and persisted.session_id:
                try:
                    await self.client.abort(persisted.session_id)
                except Exception:
                    logger.warning("Could not abort ST session for %s", run.run_id)

    async def serve(self):
        for session in self.orphan_sessions:
            await self.client.abort(session)
        while True:
            self.wake.clear()
            with self.db.engine.connect() as conn:
                value = conn.execute(
                    select(st_runs.c.data)
                    .where(st_runs.c.status == "queued")
                    .order_by(st_runs.c.data["created_at"].as_string())
                ).scalar()
            if value:
                run = STRun.model_validate(value)
                self.active_id = run.run_id
                self.active_task = asyncio.create_task(self.process(run))
                try:
                    await self.active_task
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():
                        raise
                finally:
                    self.active_id, self.active_task = None, None
            else:
                try:
                    await asyncio.wait_for(self.wake.wait(), 2)
                except TimeoutError:
                    pass


def install_st_routes(app, identity, writer, write_idempotent):
    @app.post("/api/v1/st-runs", response_model=STRun, status_code=202)
    async def start(body: STStart, auth=Depends(writer)):
        engine = app.state.st_code
        value = write_idempotent(
            app.state.db,
            auth[0],
            "/st-runs",
            auth[1],
            body,
            lambda conn: engine.create(body, auth[0], conn),
        )
        engine.wake.set()
        return value

    @app.get("/api/v1/st-runs", response_model=STRuns)
    async def history(user=Depends(identity)):
        with app.state.db.engine.connect() as conn:
            values = (
                conn.execute(
                    select(st_runs.c.data)
                    .where(st_runs.c.owner_id == user)
                    .order_by(st_runs.c.data["created_at"].as_string().desc())
                )
                .scalars()
                .all()
            )
        return STRuns(
            schema_version="1.0",
            items=[
                STRunSummary(
                    schema_version="1.0",
                    run_id=r["run_id"],
                    title=r["request"]["title"],
                    filename=r["request"]["filename"],
                    status=r["status"],
                    created_at=r["created_at"],
                )
                for r in values
            ],
        )

    @app.get("/api/v1/st-runs/{run_id}", response_model=STRun)
    async def detail(run_id: str, user=Depends(identity)):
        return app.state.st_code.get(run_id, user)

    @app.post("/api/v1/st-runs/{run_id}/cancel", response_model=STRun)
    async def cancel(run_id: str, auth=Depends(writer)):
        engine = app.state.st_code
        run = engine.get(run_id, auth[0])
        if run.status not in TERMINAL:
            run.status, run.error = "cancelled", "用户取消了任务。"
            engine.save(run)
            if engine.active_id == run_id and engine.active_task:
                engine.active_task.cancel()
        return run

    @app.get("/api/v1/st-runs/{run_id}/artifacts/{name}")
    async def download(run_id: str, name: str, user=Depends(identity)):
        run = app.state.st_code.get(run_id, user)
        output = exports(run)
        if name not in output or (run.status != "completed" and name != "original.st"):
            raise DomainError("NOT_FOUND", "文件不存在", 404)
        return Response(
            output[name].encode("utf-8"),
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{name}"'},
        )
