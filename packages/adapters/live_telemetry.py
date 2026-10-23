"""Append-only JSONL historian for the continuously sampled example plant."""

import asyncio
import fcntl
import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from packages.contracts.common import PID
from packages.domain.core import (
    COUPLED_INITIAL,
    INITIAL,
    REFERENCE,
    SCENARIOS,
    DomainError,
    example_pressure,
)

logger = logging.getLogger("demo.telemetry")


def timestamp(second: int) -> str:
    return datetime.fromtimestamp(second, timezone.utc).isoformat().replace("+00:00", "Z")


class LiveTelemetry:
    def __init__(self, state_dir: Path):
        self.root = state_dir / "telemetry"
        self.running = False
        self.task: asyncio.Task | None = None
        self.lock: TextIO | None = None

    @property
    def lock_path(self) -> Path:
        return self.root / "sampler.lock"

    def is_running(self) -> bool:
        if self.lock is not None:
            return self.running
        if not self.lock_path.exists():
            return False
        with self.lock_path.open("a+") as probe:
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(probe, fcntl.LOCK_UN)
        return False

    def path(self, scenario: str) -> Path:
        if scenario not in SCENARIOS:
            raise DomainError("SCENARIO_NOT_FOUND", status=404)
        return self.root / f"{scenario}.jsonl"

    @staticmethod
    def pid(scenario: str) -> PID:
        return (
            REFERENCE if scenario == "normal"
            else COUPLED_INITIAL if scenario == "coupled_load"
            else INITIAL
        )

    @staticmethod
    def tail(path: Path, limit: int = 121) -> list[dict]:
        if not path.exists():
            return []
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            end = stream.tell()
            if end:
                stream.seek(end - 1)
                complete = stream.read(1) == b"\n"
            else:
                complete = True
            chunks = []
            while end > 0 and sum(chunk.count(b"\n") for chunk in chunks) <= limit:
                size = min(65536, end)
                end -= size
                stream.seek(end)
                chunks.append(stream.read(size))
            lines = b"".join(reversed(chunks)).splitlines()
            if not complete:
                lines = lines[:-1]
            lines = lines[-limit:]
        try:
            return [json.loads(line) for line in lines]
        except (ValueError, UnicodeDecodeError) as exc:
            raise DomainError("DATA_INVALID", "时序记录损坏") from exc

    def append_until(self, second: int) -> None:
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        # Each scenario has its own file. Overlap the four durable flushes so
        # WSL disk latency does not add up to a missed one-second sample tick.
        # Wait for every write, propagate errors, and never backfill gaps.
        with ThreadPoolExecutor(max_workers=len(SCENARIOS)) as pool:
            list(pool.map(lambda scenario: self._append_scenario(scenario, second), SCENARIOS))

    def _append_scenario(self, scenario: str, second: int) -> None:
        path = self.path(scenario)
        if path.exists():
            # A crash may leave a partial last line. Discard only that line.
            with path.open("rb+") as stream:
                stream.seek(0, os.SEEK_END)
                end = stream.tell()
                if end:
                    stream.seek(-1, os.SEEK_END)
                    if stream.read(1) != b"\n":
                        position = end - 2
                        while position >= 0:
                            stream.seek(position)
                            if stream.read(1) == b"\n":
                                break
                            position -= 1
                        stream.truncate(position + 1)
        previous = self.tail(path, 1)
        last = int(previous[-1]["sample_second"]) if previous else None
        # Downtime is a real gap. Only the current tick is sampled; the
        # monitor waits for a complete new window after a long stop.
        if last is not None and last >= second:
            return
        with path.open("a", encoding="utf-8") as stream:
            record = {
                "schema_version": "1.0",
                "producer": "example-loop-v3",
                "control_loop_id": "feedwater-pump-demo",
                "scenario_id": scenario,
                "timestamp": timestamp(second),
                "sample_second": second,
                "pressure": example_pressure(self.pid(scenario), scenario, float(second)),
                "target_pressure": 30.0,
                "pressure_unit": "demo_unit",
                "quality": "GOOD",
                "pid": self.pid(scenario).model_dump(mode="json"),
                "pid_version": "initial-v2",
                "operating_condition_version": "examples-v2",
                "sample_kind": "LIVE",
            }
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            stream.flush()
            os.fsync(stream.fileno())

    async def start(self) -> None:
        if self.lock is not None:
            raise DomainError("LOOP_ALREADY_RUNNING", "示例回路已在运行")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = self.lock_path.open("a+")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            self.lock.close()
            self.lock = None
            raise DomainError("LOOP_ALREADY_RUNNING", "示例回路已在运行") from exc
        try:
            self.append_until(int(time.time()))
        except Exception:
            self.lock.close()
            self.lock = None
            raise
        self.running = True
        self.task = asyncio.create_task(self.run())

    async def run(self) -> None:
        try:
            while True:
                await asyncio.sleep(max(0.05, 1.01 - time.time() % 1))
                try:
                    self.append_until(int(time.time()))
                    self.running = True
                except (OSError, ValueError, DomainError):
                    self.running = False
                    logger.exception("virtual telemetry append failed; retrying")
                    await asyncio.sleep(5)
        finally:
            self.running = False

    async def close(self) -> None:
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        self.running = False
        if self.lock:
            self.lock.close()
            self.lock = None

    def latest(self, scenario: str) -> dict | None:
        rows = self.tail(self.path(scenario), 1)
        return rows[-1] if rows else None

    def window(self, scenario: str, as_of: str) -> list[dict]:
        if not self.is_running():
            raise DomainError("DEPENDENCY_NOT_READY", "示例回路未运行", 503)
        rows = self.tail(self.path(scenario), 124)
        cutoff = datetime.fromisoformat(as_of.replace("Z", "+00:00")).timestamp()
        rows = [row for row in rows if row["sample_second"] <= cutoff][-121:]
        if len(rows) != 121 or any(
            row.get("producer") != "example-loop-v3" or row.get("sample_kind") != "LIVE"
            or row["sample_second"] != rows[0]["sample_second"] + index
            for index, row in enumerate(rows)
        ):
            raise DomainError("DEPENDENCY_NOT_READY", "尚无连续 120 秒的示例回路数据", 503)
        if cutoff - rows[-1]["sample_second"] > 3:
            raise DomainError("DEPENDENCY_NOT_READY", "示例回路数据已过期", 503)
        return rows

    def window_ready(self, scenario: str, as_of: str) -> bool:
        try:
            self.window(scenario, as_of)
        except (DomainError, OSError, ValueError, KeyError, TypeError, AttributeError):
            return False
        return True
