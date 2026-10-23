"""Small tailnet-only entry point for the local demo UI and public API."""

from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
dist = Path(__file__).parent / "dashboard/dist"
backend = httpx.AsyncClient(base_url="http://127.0.0.1:18000", trust_env=False, timeout=30)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@app.on_event("shutdown")
async def close_backend():
    await backend.aclose()


@app.api_route("/api/v1/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
async def api(request: Request, path: str):
    # The local backend owns identity, CSRF, request limits, and business logic.
    # Never forward caller-supplied proxy or internal-service credentials.
    if path.startswith("/") or "\\" in path or any(
        segment in {".", ".."} for segment in path.split("/")
    ):
        return Response(status_code=404)
    blocked = {
        "host", "connection", "content-length", "authorization", "x-demo-proxy-token",
        "x-authenticated-user", "x-mcp-service-token", "x-forwarded-for",
        "x-forwarded-host", "x-forwarded-proto", "forwarded", "transfer-encoding",
        "te", "upgrade", "keep-alive", "proxy-authenticate", "proxy-authorization",
        "trailer", "expect",
    }
    headers = {k: v for k, v in request.headers.items() if k.lower() not in blocked}
    body = await request.body()
    if len(body) > 32768:
        return Response(status_code=413)
    try:
        upstream = await backend.request(
            request.method,
            "/api/v1/" + path,
            params=request.query_params,
            headers=headers,
            content=body,
        )
    except httpx.RequestError:
        return Response(status_code=502)
    response_headers = {
        k: v for k, v in upstream.headers.items()
        if k.lower() in {"content-type", "cache-control", "x-content-type-options", "referrer-policy"}
    }
    return Response(upstream.content, status_code=upstream.status_code, headers=response_headers)


@app.get("/")
async def index():
    return FileResponse(dist / "index.html", headers={"Cache-Control": "no-store"})


app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")
