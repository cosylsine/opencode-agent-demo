"""Real backend smoke: independent Monitor and B-loop Control."""

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from packages.domain.states import TERMINAL
from scripts.acceptance_login import acceptance_login


async def main(base, min_b_candidates=1):
    async with httpx.AsyncClient(base_url=base, trust_env=False, timeout=10) as client:
        await acceptance_login(client, base)
        health = (await client.get("/api/v1/health")).json()
        if not health.get("ready"):
            raise SystemExit("Real integration NOT RUN: backend/model not ready")
        evidence = []
        for scenario in ("normal", "improvable"):
            token = (await client.get("/api/v1/csrf-token")).json()["token"]
            response = await client.post(
                "/api/v1/triggers",
                json={"schema_version": "1.0", "scenario_id": scenario},
                headers={
                    "Origin": base,
                    "X-CSRF-Token": token,
                    "Idempotency-Key": str(uuid.uuid4()),
                },
            )
            response.raise_for_status()
            run_id = response.json()["run_id"]
            print(f"Monitor scenario={scenario} run_id={run_id}", flush=True)
            for _ in range(460):
                run = (await client.get("/api/v1/runs/" + run_id)).json()
                if run["status"] in TERMINAL:
                    break
                await asyncio.sleep(2)
            else:
                raise SystemExit("Real integration FAILED: deadline exceeded")
            audit = (await client.get("/api/v1/runs/" + run_id + "/events")).json()["items"]
            tools = [x["detail"] for x in audit if x["event"] == "MCP_READ"]
            roles = {x["role"] for x in tools}
            if run["source_kind"] != "EXAMPLE" or "monitor" not in roles:
                raise SystemExit("Real integration FAILED: missing EXAMPLE/Monitor MCP evidence")
            required_reads = {"get_recent_window", "calculate_metrics", "detect_state"}
            monitor_reads = {}
            for item in tools:
                if item.get("role") == "monitor":
                    monitor_reads.setdefault(item.get("request_id"), []).append(item.get("tool"))
            if None in monitor_reads or any(
                len(reads) != 3 or any(reads.count(tool) != 1 for tool in required_reads)
                for reads in monitor_reads.values()
            ):
                raise SystemExit("Real integration FAILED: Monitor did not read all three tools once per request")
            if scenario == "normal" and (
                run["status"] != "NO_TUNING_REQUIRED" or run["stop_reason"] != "NORMAL_STABLE"
            ):
                raise SystemExit("Real integration FAILED: normal Monitor result was not stable")
            if scenario == "improvable" and (
                run["status"] != "MANUAL_REVIEW_REQUIRED"
                or run["stop_reason"] != "SIMULATION_TUNING_SUGGESTED"
                or run["iterations"]
                or "control" in roles
            ):
                raise SystemExit("Real integration FAILED: Monitor was not decoupled")
            if run["status"] in ("FAILED_FINAL", "STOPPED_TIMEOUT", "CANCELLED"):
                raise SystemExit("Real integration FAILED: " + run["status"])
            evidence.append(
                {
                    "scenario": scenario,
                    "run_id": run_id,
                    "status": run["status"],
                    "source_kind": run["source_kind"],
                    "tools": tools,
                    "iterations": len(run["iterations"]),
                }
            )
        token = (await client.get("/api/v1/csrf-token")).json()["token"]
        headers = {"Origin": base, "X-CSRF-Token": token, "Idempotency-Key": str(uuid.uuid4())}
        response = await client.post("/api/v1/tuning-runs", json={"schema_version": "1.0"}, headers=headers)
        response.raise_for_status()
        b_id = response.json()["run_id"]
        print(f"B-loop run_id={b_id}", flush=True)
        for _ in range(180 * min_b_candidates):
            run = (await client.get("/api/v1/tuning-runs/" + b_id)).json()
            if len(run["trials"]) >= min_b_candidates + 1 or run["status"] in ("COMPLETED", "FAILED", "CANCELLED", "AGENT_ABSTAINED", "NO_SAFE_CANDIDATE"):
                break
            await asyncio.sleep(2)
        audit = (await client.get("/api/v1/tuning-runs/" + b_id + "/events")).json()["items"]
        if run["status"] in ("FAILED", "CANCELLED") or len(run["trials"]) < min_b_candidates + 1:
            raise SystemExit("Real integration FAILED: insufficient evaluated B candidates; status=" + run["status"])
        candidates = [trial for trial in run["trials"] if trial["iteration"] > 0]
        for trial in candidates:
            if not trial.get("agent_output") or len(trial.get("result", {}).get("scenarios", {})) != 4:
                raise SystemExit("Real integration FAILED: missing Control output or four-scenario evaluation")
        tools = [x["detail"] for x in audit if x["event"] == "MCP_READ"]
        if run["source_kind"] != "SIMULATION" or sum(
            item.get("role") == "control" and item.get("tool") == "get_tuning_context" for item in tools
        ) < min_b_candidates:
            raise SystemExit("Real integration FAILED: missing per-round Control MCP evidence")
        if run["status"] not in ("COMPLETED", "AGENT_ABSTAINED", "FAILED", "CANCELLED", "NO_SAFE_CANDIDATE"):
            headers["Idempotency-Key"] = str(uuid.uuid4())
            cancelled = await client.post("/api/v1/tuning-runs/" + b_id + "/cancel", json={"schema_version": "1.0"}, headers=headers)
            cancelled.raise_for_status()
            for _ in range(15):
                terminal = (await client.get("/api/v1/tuning-runs/" + b_id)).raise_for_status().json()
                if terminal["status"] in ("COMPLETED", "AGENT_ABSTAINED", "CANCELLED", "NO_SAFE_CANDIDATE"):
                    break
                await asyncio.sleep(2)
            else:
                raise SystemExit("Real integration FAILED: B acceptance task did not stop")
        else:
            terminal = run
        evidence.append({"run_id": b_id, "source_kind": run["source_kind"], "status": run["status"],
                         "final_status": terminal["status"],
                         "trials": len(run["trials"]), "events": len(audit), "tools": tools,
                         "min_b_candidates": min_b_candidates})
        path = Path(os.environ.get("DEMO_STATE_DIR", "var/backend")) / "real-integration.json"
        path.write_text(
            json.dumps(
                {"schema_version": "1.0", "evidence": evidence}, ensure_ascii=False, indent=2
            )
        )
        print("Real integration evidence saved to " + str(path))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://127.0.0.1:18000")
    parser.add_argument("--min-b-candidates", type=int, choices=range(1, 13), default=1)
    args = parser.parse_args()
    asyncio.run(main(args.base, args.min_b_candidates))
