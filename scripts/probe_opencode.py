"""Isolated, authenticated OpenCode compatibility probe. Never reads user credentials."""

import base64
import json
import os
import secrets
import socket
import subprocess
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
state = ROOT / "var" / "d0"
state.mkdir(parents=True, exist_ok=True, mode=0o700)
config = state / "opencode.json"
config.write_text(
    json.dumps(
        {
            "$schema": "https://opencode.ai/config.json",
            "autoupdate": False,
            "share": "disabled",
            "permission": {"*": "deny"},
            "plugin": [],
            "agent": {
                "probe": {
                    "mode": "primary",
                    "description": "Read-only probe",
                    "permission": {"*": "deny"},
                    "prompt": "Return only the requested structured result.",
                }
            },
        }
    )
)
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
password = secrets.token_urlsafe(32)
env = dict(os.environ)
env.update(
    OPENCODE_CONFIG=str(config),
    OPENCODE_SERVER_PASSWORD=password,
    OPENCODE_DISABLE_AUTOUPDATE="true",
    OPENCODE_DISABLE_PROJECT_CONFIG="true",
    XDG_CONFIG_HOME=str(state / "config"),
    XDG_DATA_HOME=str(state / "data"),
    XDG_CACHE_HOME=str(state / "cache"),
    XDG_STATE_HOME=str(state / "state"),
)
# Do not import inherited inline configuration.
env.pop("OPENCODE_CONFIG_CONTENT", None)
headers = {"Authorization": "Basic " + base64.b64encode(("opencode:" + password).encode()).decode()}


def get(path, data=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}" + path,
        data=None if data is None else json.dumps(data).encode(),
        headers={**headers, "Content-Type": "application/json"},
    )
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
        req, timeout=5
    ) as response:
        return json.load(response)


with (state / "server.log").open("w") as log:
    process = subprocess.Popen(
        [
            os.environ.get("OPENCODE_BIN", str(Path(__file__).resolve().parents[1] / "bin/opencode")),
            "serve",
            "--pure",
            "--hostname",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=state,
        env=env,
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    try:
        for _ in range(60):
            try:
                health = get("/global/health")
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("OpenCode startup failed; inspect private var/d0/server.log")
        doc = get("/doc")
        (ROOT / "docs/api/opencode-1.18.31.openapi.json").write_text(json.dumps(doc, indent=2))
        session = get("/session", {"title": "D0 isolated empty-session probe"})
        abort = get("/session/" + session["id"] + "/abort", {})
        statuses = get("/session/status")
        agents = get("/agent")
        result = {
            "health": health,
            "doc_paths": len(doc["paths"]),
            "empty_session_abort": abort,
            "empty_session_status": statuses.get(session["id"], "absent/idle"),
            "probe_agent_present": any(a["name"] == "probe" for a in agents),
            "model_probe": "NOT_RUN: provider/model not configured",
            "note": "Empty-session abort does not verify in-flight cancellation.",
        }
        (ROOT / "docs/runbooks/d0-probe.json").write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
