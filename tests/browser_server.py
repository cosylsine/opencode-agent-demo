"""Browser test entry point ONLY. Production never imports this module."""

import os
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path

from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from apps.orchestrator.config import Settings
from apps.orchestrator.main import create_app
from packages.adapters.example import ExampleObservationSource
from tests.fakes import FakeClient

settings = Settings.from_env()
if not settings.provider:
    settings = replace(settings, provider="local-vllm", model="TEST-office-protocol")
app = create_app(settings, client_factory=FakeClient, source_factory=ExampleObservationSource)
original_lifespan = app.router.lifespan_context


@asynccontextmanager
async def browser_lifespan(application):
    async with original_lifespan(application):
        if not application.state.platform.initialized():
            application.state.platform.initialize_admin(
                "browser-admin", "浏览器测试管理员", "browser-test-password"
            )

        async def office_chat(snapshot, history, files, token, on_session):
            on_session("ses_browser_test")
            if snapshot["id"] == "builtin-agent-general-assistant":
                return "测试通用助手：" + history[-1]["content"]
            tools = snapshot["content"]["tools"]
            if "generate_document" in tools:
                await application.state.office.call(
                    token,
                    "generate_document",
                    {
                        "title": "浏览器测试产物",
                        "paragraphs": [history[-1]["content"]],
                        "filename": "测试结果.docx",
                    },
                )
            elif "export_text" in tools:
                await application.state.office.call(
                    token,
                    "export_text",
                    {"content": history[-1]["content"], "filename": "测试结果.md"},
                )
            return "测试驱动已完成任务，产物由实际文件工具生成。"

        application.state.client.office_chat = office_chat
        yield


app.router.lifespan_context = browser_lifespan

# Test a staged frontend before replacing the files served by the live gateway.
if browser_dist := os.environ.get("DEMO_BROWSER_DIST"):
    dist = Path(browser_dist).resolve(strict=True)
    app.router.routes[:] = [
        route for route in app.router.routes if getattr(route, "path", None) not in {"/", "/assets"}
    ]

    @app.get("/")
    async def browser_index():
        return FileResponse(dist / "index.html")

    app.mount("/assets", StaticFiles(directory=dist / "assets"), name="browser-assets")
