"""Record a restart baseline or verify WSL services and persisted business records."""

import argparse
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "var/wsl-restart-baseline.json"
EVIDENCE = ROOT / "docs/runbooks/wsl-restart-verification.json"
UNITS = [f"719agent-demo-{name}.service" for name in ("loop", "backend", "opencode", "tailnet")]


def instance():
    return {
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "init_start_ticks": Path("/proc/1/stat").read_text().rsplit(")", 1)[1].split()[19],
    }


def snapshot(client):
    return {
        endpoint: [item["run_id"] for item in client.get("/api/v1/" + endpoint).json()["items"]]
        for endpoint in ("runs", "tuning-runs")
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["baseline", "after-restart"])
    args = parser.parse_args()
    with httpx.Client(base_url="http://127.0.0.1:18080", trust_env=False, timeout=10) as client:
        if args.phase == "baseline":
            response = client.get("/api/v1/health")
            response.raise_for_status()
            if not response.json().get("ready"):
                raise SystemExit("Baseline rejected: deployment is not ready")
            for unit in UNITS:
                subprocess.run(["systemctl", "--user", "is-enabled", unit], check=True)
            data = {"instance": instance(), "history": snapshot(client)}
            BASELINE.write_text(json.dumps(data, indent=2) + "\n")
            os.chmod(BASELINE, 0o600)
            print("Restart baseline saved; no secret values included.")
            return

        baseline = json.loads(BASELINE.read_text())
        if baseline["instance"] == instance():
            raise SystemExit("WSL restart was not observed; refusing to certify it")
        deadline = time.monotonic() + 300
        while time.monotonic() < deadline:
            try:
                response = client.get("/api/v1/health")
                response.raise_for_status()
                health = response.json()
                if health.get("ready"):
                    break
            except (httpx.HTTPError, ValueError):
                pass
            print("Waiting for services/model and a new continuous 120-second window...", flush=True)
            time.sleep(10)
        else:
            raise SystemExit("WSL did not become ready; keep the server9 application running")
        for unit in UNITS:
            subprocess.run(["systemctl", "--user", "is-active", "--quiet", unit], check=True)
        history = snapshot(client)
        if history != baseline["history"]:
            raise SystemExit("Persisted history differs from the pre-restart baseline; inspect before cutover")
        for path in ("/internal/v1/tools/get_recent_window", "/live", "/docs"):
            if client.get(path).status_code != 404:
                raise SystemExit("Unexpected gateway exposure: " + path)
        evidence = {
            "verified_at": datetime.now(timezone.utc).isoformat(),
            "before": baseline["instance"], "after": instance(),
            "restart_observed": True, "health": health,
            "history_preserved": True, "internal_routes_blocked": True,
            "units": UNITS,
        }
        EVIDENCE.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
        print("WSL restart verification passed: " + str(EVIDENCE))


if __name__ == "__main__":
    main()
