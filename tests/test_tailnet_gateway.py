import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.testclient import TestClient

from apps.tailnet_gateway import create_app


def test_login_cookies_are_separate_and_multiple_set_cookie_headers_survive():
    backend = FastAPI()
    received = []

    @backend.api_route("/api/v1/{path:path}", methods=["GET", "POST"])
    async def api(request: Request, path: str):
        received.append(dict(request.headers))
        if path == "auth/login":
            response = Response("signed in")
            response.set_cookie("office_session", "alice", httponly=True, samesite="strict")
            response.set_cookie("office_anon", "nonce", httponly=True, samesite="strict")
            return response
        return {"cookie": request.headers.get("cookie", "")}

    gateway = create_app(transport=httpx.ASGITransport(app=backend))
    with TestClient(gateway) as alice, TestClient(gateway) as anonymous:
        response = alice.post("/api/v1/auth/login", headers={
            "Origin": "http://100.64.0.17:18080", "X-CSRF-Token": "csrf-value",
        })
        assert len(response.headers.get_list("set-cookie")) == 2
        assert all("HttpOnly" in cookie for cookie in response.headers.get_list("set-cookie"))
        assert "office_session=alice" in alice.get("/api/v1/auth/me").json()["cookie"]
        assert anonymous.get("/api/v1/auth/me").json()["cookie"] == ""
        assert received[0]["origin"] == "http://100.64.0.17:18080"
        assert received[0]["x-csrf-token"] == "csrf-value"


def test_download_headers_and_content_are_preserved():
    def respond(request):
        assert request.headers["cookie"] == "office_session=alice"
        assert request.url.query == b"version=2&version=3"
        return httpx.Response(200, content=b"document", headers={
            "Content-Type": "application/octet-stream",
            "Content-Disposition": "attachment; filename*=utf-8''%E6%8A%A5%E5%91%8A.docx",
            "Cache-Control": "no-store", "X-OpenCode-Demo": "lan-fix-20261014",
            "X-Internal-Secret": "must-not-leak",
        })

    with TestClient(create_app(transport=httpx.MockTransport(respond))) as client:
        response = client.get("/api/v1/files/123/download?version=2&version=3",
                              headers={"Cookie": "office_session=alice"})
    assert response.content == b"document"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "x-internal-secret" not in response.headers


def test_twenty_mib_file_upload_is_allowed_with_multipart_overhead():
    def respond(request):
        assert request.headers["content-type"].startswith("multipart/form-data;")
        assert len(request.content) > 20 * 1024 * 1024
        assert len(request.content) < 21 * 1024 * 1024
        return httpx.Response(201, json={"uploaded": True})

    with TestClient(create_app(transport=httpx.MockTransport(respond))) as client:
        response = client.post("/api/v1/files", files={
            "file": ("large.txt", b"x" * (20 * 1024 * 1024), "text/plain"),
        })
    assert response.status_code == 201


@pytest.mark.asyncio
@pytest.mark.parametrize("path,limit", [
    ("/api/v1/files", 1024 * 1024 + 128),
    ("/api/v1/auth/login", 32768),
    ("/api/v1/conversations", 2 * 1024 * 1024),
])
async def test_chunked_body_stops_at_limit_before_upstream(monkeypatch, path, limit):
    monkeypatch.setenv("OFFICE_MAX_FILE_BYTES", "128")
    consumed = []

    def unexpected(request):
        pytest.fail("Oversized request reached upstream")

    async def chunks():
        consumed.append(1)
        yield b"x" * limit
        consumed.append(2)
        yield b"x"
        consumed.append(3)
        yield b"must not read after exceeding the limit"

    app = create_app(transport=httpx.MockTransport(unexpected))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                base_url="http://test") as client:
        response = await client.post(path, content=chunks())
    assert response.status_code == 413
    assert consumed == [1, 2]


def test_office_json_keeps_backend_limits_and_other_json_stays_small():
    def respond(request):
        return httpx.Response(200)

    with TestClient(create_app(transport=httpx.MockTransport(respond))) as client:
        assert client.post("/api/v1/st-runs", content=b"x" * 65536).status_code == 200
        assert client.post("/api/v1/st-runs", content=b"x" * (2 * 1024 * 1024 + 1)).status_code == 413
        assert client.post("/api/v1/auth/login", content=b"x" * 32769).status_code == 413
        assert client.post("/api/v1/files", content=b"x", headers={"Content-Length": "bad"}).status_code == 422


@pytest.mark.parametrize("path", [
    "/internal/v1/office", "/docs", "/openapi.json", "/api/v1/internal/v1/office",
    "/api/v1/%2e%2e/%2e%2e/internal/v1/office",
    "/api/v1/%252e%252e/%252e%252e/internal/v1/office",
    "/api/v1/%5cinternal", "/api/v1//internal", "/api/v1/a/%2e/b",
])
def test_private_and_traversal_paths_never_reach_upstream(path):
    def unexpected(request):
        pytest.fail("Private route reached upstream")

    with TestClient(create_app(transport=httpx.MockTransport(unexpected))) as client:
        assert client.get(path).status_code == 404


def test_proxy_credentials_and_connection_headers_are_not_forwarded():
    forbidden = {
        "Authorization": "Bearer private", "X-Demo-Proxy-Token": "private",
        "X-Authenticated-User": "admin", "X-MCP-Service-Token": "private",
        "X-Forwarded-For": "127.0.0.1", "X-Forwarded-Host": "private",
        "X-Forwarded-Proto": "https", "Forwarded": "for=127.0.0.1",
        "X-Connection-Only": "private", "Proxy-Authorization": "private",
    }

    def respond(request):
        assert request.headers["host"] == "127.0.0.1:18000"
        assert all(name not in request.headers for name in forbidden)
        assert request.headers["cookie"] == "office_session=alice"
        assert request.headers["origin"] == "http://100.64.0.17:18080"
        assert request.headers["x-csrf-token"] == "csrf"
        return httpx.Response(200)

    with TestClient(create_app(transport=httpx.MockTransport(respond))) as client:
        response = client.post("/api/v1/auth/logout", headers={
            **forbidden, "Connection": "X-Connection-Only", "Cookie": "office_session=alice",
            "Origin": "http://100.64.0.17:18080", "X-CSRF-Token": "csrf",
        })
    assert response.status_code == 200


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_upstream_error_status_and_body_survive(status):
    with TestClient(create_app(transport=httpx.MockTransport(
        lambda request: httpx.Response(status, json={"error": "backend error"})
    ))) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == status
    assert response.json() == {"error": "backend error"}


def test_connection_failure_is_502():
    def unavailable(request):
        raise httpx.ConnectError("private upstream details", request=request)

    with TestClient(create_app(transport=httpx.MockTransport(unavailable))) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 502
    assert "private" not in response.text


def test_redirect_does_not_make_an_internal_request():
    calls = []

    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "http://127.0.0.1:18000/internal/v1"})

    with TestClient(create_app(transport=httpx.MockTransport(respond))) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 302
    assert "location" not in response.headers
    assert calls == ["http://127.0.0.1:18000/api/v1/health"]
