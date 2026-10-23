"""Regression check for restart TIME_WAIT vs an actually occupied TCP port."""

import os
import socket
import sys

sys.path.insert(0, "/opt/deploy")
from runtime import STATE, preflight

STATE.mkdir(exist_ok=True)
os.environ["VLLM_BASE_URL"] = "http://127.0.0.1:8000/v1"
os.environ["OPENCODE_PORT"] = "14096"
with socket.socket() as server:
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", 0))
    port = server.getsockname()[1]
    os.environ["APP_PORT"] = str(port)
    server.listen()
    try:
        preflight()
    except ValueError:
        print("PASS: active listener rejected")
    else:
        raise AssertionError("Active listener was not rejected")
    with socket.create_connection(("127.0.0.1", port)) as client:
        accepted, _ = server.accept()
        accepted.close()  # Server side enters TIME_WAIT after client close.
        assert client.recv(1) == b""
preflight()
print("PASS: recently closed listener accepted; immediate restart is safe")
