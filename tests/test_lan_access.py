"""LAN clients use public APIs; internal APIs and write checks stay restricted."""

import pytest

from apps.orchestrator.config import Settings
from apps.orchestrator.main import create_app
from tests.auth_helpers import AuthenticatedClient as TestClient
from tests.fakes import FakeClient


@pytest.fixture
def lan_app(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_PORT", "3000")
    monkeypatch.setenv("DEMO_ALLOWED_CIDRS", "10.70.0.0/17,10.71.0.8/32")
    return create_app(
        Settings(state_dir=tmp_path, allowed_origin="http://10.70.100.220:3000",
                 service_token="s" * 40),
        client_factory=FakeClient,
        worker_enabled=False,
    )


@pytest.mark.parametrize("peer", ["10.70.100.42", "10.70.42.5", "10.71.0.8", "127.0.0.1", "::1"])
def test_whitelisted_clients_can_read_and_write(lan_app, peer):
    with TestClient(lan_app, client=(peer, 12345)) as client:
        token = client.get("/api/v1/csrf-token")
        assert token.status_code == 200
        response = client.post(
            "/api/v1/chat/sessions",
            json={"schema_version": "1.0"},
            headers={"Origin": "http://10.70.100.220:3000",
                     "X-CSRF-Token": token.json()["token"], "Idempotency-Key": "new"},
        )
        assert response.status_code == 201, response.text
        assert response.headers["X-OpenCode-Demo"] == "lan-fix-20261014"


@pytest.mark.parametrize("peer", ["10.70.128.1", "10.71.0.9", "192.168.1.42", "unknown"])
def test_other_clients_cannot_spoof_forwarded_address(lan_app, peer):
    with TestClient(lan_app, client=(peer, 12345)) as client:
        response = client.get(
            "/api/v1/csrf-token",
            headers={"X-Forwarded-For": "10.70.100.42", "X-Real-IP": "127.0.0.1"},
        )
        assert response.status_code == 401


def test_lan_clients_cannot_call_internal_api_even_with_service_token(lan_app):
    with TestClient(lan_app, client=("10.70.100.42", 12345)) as client:
        response = client.post(
            "/internal/v1/tools/get_observation",
            json={},
            headers={"Authorization": "Bearer " + "s" * 40, "X-Forwarded-For": "127.0.0.1"},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"


@pytest.mark.parametrize("origin", ["http://127.0.0.1:3000", "http://localhost:3000"])
def test_current_port_local_origins_remain_usable(lan_app, origin):
    with TestClient(lan_app, client=("127.0.0.1", 12345)) as client:
        token = client.get("/api/v1/csrf-token").json()["token"]
        response = client.post(
            "/api/v1/chat/sessions", json={"schema_version": "1.0"},
            headers={"Origin": origin, "X-CSRF-Token": token, "Idempotency-Key": "local"},
        )
        assert response.status_code == 201


@pytest.mark.parametrize("origin,valid_token", [
    ("http://evil.invalid:3000", True),
    ("http://localhost:18000", True),
    ("http://10.70.100.220:3000", False),
])
def test_lan_writes_still_require_exact_origin_and_csrf(lan_app, origin, valid_token):
    with TestClient(lan_app, client=("10.70.100.42", 12345)) as client:
        token = client.get("/api/v1/csrf-token").json()["token"]
        response = client.post(
            "/api/v1/chat/sessions", json={"schema_version": "1.0"},
            headers={"Origin": origin, "X-CSRF-Token": token if valid_token else "wrong",
                     "Idempotency-Key": "invalid"},
        )
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "CSRF_REJECTED"


def test_absent_whitelist_keeps_loopback_only(tmp_path, monkeypatch):
    monkeypatch.delenv("DEMO_ALLOWED_CIDRS", raising=False)
    app = create_app(Settings(state_dir=tmp_path), client_factory=FakeClient, worker_enabled=False)
    with TestClient(app, client=("10.70.100.42", 12345)) as client:
        assert client.get("/api/v1/csrf-token").status_code == 401
