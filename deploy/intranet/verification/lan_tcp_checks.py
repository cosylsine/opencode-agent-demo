"""Real LAN-address TCP checks inside a --network none, NET_ADMIN test container."""

import fcntl
import json
import os
import re
import socket
import struct
import subprocess
import sys
import time

import httpx


def alias(label, address):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for command, value in ((0x8916, address), (0x891C, "255.255.255.255")):
            request = struct.pack("16sH2s4s8s", label.encode(), socket.AF_INET,
                                  b"\0" * 2, socket.inet_aton(value), b"\0" * 8)
            fcntl.ioctl(sock.fileno(), command, request)


def main():
    if os.environ.get("LAN_REHEARSAL_ISOLATED") != "1":
        raise ValueError("Only run in the isolated test container, never on the host")
    for label, address in [("lo:server", "10.70.100.220"), ("lo:allowed", "10.70.42.5"),
                           ("lo:denied", "10.70.128.1")]:
        alias(label, address)
    service = subprocess.Popen([sys.executable, "/opt/rehearsal/rehearsal_entry.py"],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        base = "http://10.70.100.220:3000"
        with httpx.Client(base_url=base, trust_env=False, timeout=10,
                          transport=httpx.HTTPTransport(local_address="10.70.42.5")) as client:
            for _ in range(120):
                if service.poll() is not None:
                    raise RuntimeError(service.stdout.read())
                try:
                    response = client.get("/api/v1/auth/me")
                    if response.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.25)
            else:
                raise RuntimeError("LAN API did not become reachable")
            anonymous = response.json()["csrf_token"]
            login = client.post("/api/v1/auth/login",
                                json={"username": "office-test-admin", "password": "office-protocol-test-password"},
                                headers={"Origin": base, "X-CSRF-Token": anonymous})
            assert login.status_code == 200, login.text
            token = client.get("/api/v1/csrf-token").json()["token"]
            html = client.get("/?lancheck=rebuild-20261018-tcp")
            assert html.status_code == 200 and "chrome86-compat" in html.text
            assets = re.findall(r'(?:src|href)="(/assets/[^\"]+)"', html.text)
            assert assets and all(client.get(asset).status_code == 200 for asset in assets)
            headers = {"Origin": base, "X-CSRF-Token": token, "Idempotency-Key": "tcp-new"}
            response = client.post("/api/v1/chat/sessions", json={"schema_version": "1.0"}, headers=headers)
            assert response.status_code == 201, response.text
            for bad in ({**headers, "Origin": "http://evil.invalid:3000"},
                        {**headers, "X-CSRF-Token": "wrong"}):
                response = client.post("/api/v1/chat/sessions", json={"schema_version": "1.0"}, headers=bad)
                assert response.status_code == 403, response.text
            response = client.post("/internal/v1/tools/get_recent_window", json={})
            assert response.status_code == 403
            assert response.headers["X-OpenCode-Demo"] == "lan-fix-20261014"
        with httpx.Client(base_url=base, trust_env=False, timeout=10,
                          transport=httpx.HTTPTransport(local_address="10.70.128.1")) as client:
            response = client.get("/api/v1/csrf-token", headers={"X-Forwarded-For": "10.70.42.5"})
            assert response.status_code == 401, response.text
        print(json.dumps({"network": "none", "model": "TEST STUB, not Qwen inference",
                          "server": base, "allowed_source": "10.70.42.5", "denied_source": "10.70.128.1",
                          "whitelist": "10.70.0.0/17", "static_assets": len(assets),
                          "allowed_read_write": "passed", "bad_origin": "rejected",
                          "bad_csrf": "rejected", "internal_from_lan": "rejected",
                          "spoofed_forwarded_source": "rejected"}, ensure_ascii=False, indent=2))
    finally:
        service.terminate()
        try:
            service.communicate(timeout=40)
        except subprocess.TimeoutExpired:
            service.kill()
            service.communicate()


if __name__ == "__main__":
    main()
