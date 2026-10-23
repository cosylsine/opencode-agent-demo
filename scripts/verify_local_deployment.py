"""Verify the dedicated local unit across restart and backup/restore (no model claims)."""

import json
import subprocess
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.backup import backup, restore

ROOT = Path(__file__).resolve().parents[1]
UNIT = "719agent-demo-backend.service"
LOOP_UNIT = "719agent-demo-loop.service"


def systemctl(action, unit=UNIT):
    subprocess.run(["systemctl", "--user", action, unit], check=True, capture_output=True)


def health():
    with httpx.Client(trust_env=False, timeout=2) as client:
        for _ in range(50):
            try:
                response = client.get("http://127.0.0.1:18000/api/v1/health")
                response.raise_for_status()
                return response.json()
            except httpx.HTTPError:
                time.sleep(0.1)
    raise RuntimeError("backend did not start")


def main():
    health()
    with httpx.Client(trust_env=False) as client:
        history_before = client.get("http://127.0.0.1:18000/api/v1/runs").json()
    systemctl("restart")
    after = health()
    with httpx.Client(trust_env=False) as client:
        history_after = client.get("http://127.0.0.1:18000/api/v1/runs").json()
    if history_before != history_after:
        raise RuntimeError("history changed after restart")
    stamp = str(time.time_ns())
    backup_path = ROOT / "var/backups" / stamp
    restored = ROOT / "var/restore-checks" / stamp
    loop_was_active = subprocess.run(
        ["systemctl", "--user", "is-active", "--quiet", LOOP_UNIT],
        capture_output=True,
    ).returncode == 0
    systemctl("stop")
    if loop_was_active:
        systemctl("stop", LOOP_UNIT)
    try:
        backup(ROOT / "var/backend", backup_path)
        restore(backup_path, restored)
    finally:
        if loop_was_active:
            systemctl("start", LOOP_UNIT)
        systemctl("start")
    health()
    result = {
        "schema_version": "1.0",
        "unit": UNIT,
        "active": True,
        "bind": "127.0.0.1:18000",
        "restart_history_equal": True,
        "history_count": len(history_after["items"]),
        "backup_restore_hash_verified": True,
        "model_ready": after["ready"],
        "note": "Empty production history verifies availability only; non-empty history restoration is covered by automated Fake tests.",
        "external_tls": "NOT_CONFIGURED",
    }
    (ROOT / "docs/runbooks/local-deployment.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
