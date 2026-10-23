"""Tailnet UI/API proxy; authentication and business limits belong to the backend."""

import os
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.requests import ClientDisconnect

BACKEND_URL = "http://127.0.0.1:18000"
BLOCKED_REQUEST_HEADERS = {
    "host", "connection", "content-length", "authorization", "x-demo-proxy-token",
    "x-authenticated-user", "x-mcp-service-token", "x-forwarded-for",
    "x-forwarded-host", "x-forwarded-proto", "forwarded", "transfer-encoding",
    "te", "upgrade", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "trailer", "expect",
}
RESPONSE_HEADERS = {
    "content-type", "cache-control", "x-content-type-options", "referrer-policy",
    "content-disposition", "set-cookie", "etag", "last-modified", "vary",
    "accept-ranges", "content-range", "retry-after", "x-opencode-demo",
}


def request_limit(path: str, method: str) -> int:
    # Match orchestrator.main.request_limits, including multipart overhead.
    if path == "files" and method == "POST":
        return int(os.getenv("OFFICE_MAX_FILE_BYTES", str(20 * 1024 * 1024))) + 1024 * 1024
    if path.startswith(("skills", "agents", "conversations", "tool-executions", "st-runs")):
        return 2 * 1024 * 1024
    return 32768


def create_app(*, transport=None, dist_dir: Path | None = None):
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    dist = dist_dir or Path(__file__).parent / "dashboard/dist"

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.api_route("/api/v1/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def api(request: Request, path: str):
        # ASGI already decoded the URL once. Reject residual escapes so another
        # decode at the upstream cannot turn an apparently public path into traversal.
        if (
            path.startswith("/") or "\\" in path or "%" in path
            or any(segment in {".", ".."} for segment in path.split("/"))
            or path == "internal" or path.startswith("internal/")
        ):
            return Response(status_code=404)
        limit = request_limit(path, request.method)
        try:
            length = int(request.headers.get("content-length", "0"))
            if length < 0:
                raise ValueError
        except ValueError:
            return Response(status_code=422)
        if length > limit:
            return Response(status_code=413)
        # Bound bytes as they arrive, including chunked requests without a length.
        # Do not send partially accepted uploads to the backend.
        body = bytearray()
        try:
            async for chunk in request.stream():
                if len(body) + len(chunk) > limit:
                    return Response(status_code=413)
                body.extend(chunk)
        except ClientDisconnect:
            return Response(status_code=400)
        connection_headers = {
            item.strip().lower()
            for item in request.headers.get("connection", "").split(",")
        }
        headers = [
            (key, value) for key, value in request.headers.raw
            if key.decode("latin-1").lower() not in BLOCKED_REQUEST_HEADERS | connection_headers
        ]
        url = httpx.URL(BACKEND_URL).copy_with(
            path="/api/v1/" + path, query=request.scope.get("query_string") or None
        )
        try:
            # A client is scoped to this one request. A shared httpx cookie jar
            # would retain a user's login cookie and inject it into other users'
            # requests. Redirects must not create a second, implicit request.
            async with httpx.AsyncClient(
                trust_env=False, timeout=30, transport=transport, follow_redirects=False
            ) as backend:
                upstream = await backend.request(
                    request.method, url, headers=headers, content=bytes(body)
                )
        except httpx.RequestError:
            return Response(status_code=502)
        response = Response(upstream.content, status_code=upstream.status_code)
        # raw headers preserve repeated Set-Cookie values. httpx decoded response
        # bytes, so let Response compute length and omit upstream content-encoding.
        response.raw_headers.extend(
            (key, value) for key, value in upstream.headers.raw
            if key.decode("latin-1").lower() in RESPONSE_HEADERS
        )
        return response

    @app.get("/")
    async def index():
        return FileResponse(dist / "index.html", headers={"Cache-Control": "no-store"})

    app.mount("/assets", StaticFiles(directory=dist / "assets", check_dir=False), name="assets")
    return app


app = create_app()
