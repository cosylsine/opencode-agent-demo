"""File-based local simulator protocol for an air-gapped deployment.

The operator configures argv once. Candidate parameters only enter a JSON input
file, never a command line or shell. The simulator writes a JSON result file.
"""

import asyncio
import fcntl
import hashlib
import json
import os
import signal
from pathlib import Path
from typing import Annotated, Literal, Protocol

from pydantic import Field, JsonValue, model_validator

from packages.contracts.common import ID, PID, Model, Number, SourceKind, uid
from packages.domain.core import canonical


class SimulationInput(Model):
    request_id: ID
    control_loop_id: str
    scenario_id: str
    parameters: PID
    parameter_version: str
    context: dict[str, JsonValue] = Field(default_factory=dict, max_length=64)

    @model_validator(mode="after")
    def bounded_context(self):
        if len(canonical(self.context)) > 65536:
            raise ValueError("Simulator context is too large")
        return self


class SimulationSample(Model):
    elapsed_seconds: Annotated[Number, Field(ge=0)]
    signals: dict[str, Number]


class SimulationOutput(Model):
    request_id: ID
    source_kind: Literal[SourceKind.SIMULATION] = SourceKind.SIMULATION
    objective: Number
    metrics: dict[str, Number]
    samples: Annotated[list[SimulationSample], Field(max_length=10000)]

    @model_validator(mode="after")
    def ordered_samples(self):
        times = [point.elapsed_seconds for point in self.samples]
        if any(right <= left for left, right in zip(times, times[1:])):
            raise ValueError("Sample times must increase")
        return self


class Simulator(Protocol):
    async def evaluate(self, request: SimulationInput) -> SimulationOutput: ...


class SimulatorError(Exception):
    pass


class JsonlProgram(Protocol):
    """Existing trial protocol: manifest.json in cwd, result.tmp as JSONL."""

    async def start(self, job: Path, inherited_fd: int) -> asyncio.subprocess.Process: ...


class LocalJsonlProgram:
    """Adapter for a pinned offline program that emits the legacy pressure series."""

    def __init__(self, argv: tuple[str, ...], pythonpath: str | None = None):
        if not argv or not Path(argv[0]).is_absolute():
            raise ValueError("Program executable must be an absolute path")
        self.argv = argv
        self.pythonpath = pythonpath

    async def start(self, job: Path, inherited_fd: int) -> asyncio.subprocess.Process:
        env = {"PATH": os.environ.get("PATH", "")}
        if self.pythonpath:
            env["PYTHONPATH"] = self.pythonpath
        return await asyncio.create_subprocess_exec(
            *self.argv,
            cwd=job,
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
            pass_fds=(inherited_fd,),
        )


class OfflineProcessSimulator:
    """Run a pinned local executable with bounded time and validated output."""

    def __init__(
        self,
        argv: tuple[str, ...],
        work_root: Path,
        timeout_seconds: float = 120,
    ):
        if not argv or not Path(argv[0]).is_absolute():
            raise ValueError("Simulator executable must be an absolute path")
        if any("\x00" in part for part in argv):
            raise ValueError("Invalid simulator argv")
        if not 0 < timeout_seconds <= 3600:
            raise ValueError("Invalid simulator timeout")
        self.argv = argv
        self.work_root = work_root
        self.timeout_seconds = timeout_seconds

    async def evaluate(self, request: SimulationInput) -> SimulationOutput:
        self.work_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        job = self.work_root / request.request_id
        job.mkdir(mode=0o700, exist_ok=True)
        if job.is_symlink():
            raise SimulatorError("Invalid job directory")
        input_path = job / "input.json"
        output_path = job / "output.json"
        receipt_path = job / "receipt.json"
        payload = request.model_dump(mode="json")
        payload["input_sha256"] = hashlib.sha256(canonical(payload).encode()).hexdigest()
        with (job / "job.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SimulatorError("Simulator request is still running") from None
            if input_path.exists():
                if input_path.is_symlink() or json.loads(input_path.read_text()) != payload:
                    raise SimulatorError("Request ID reused with different input")
            else:
                fd = os.open(input_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as out:
                    json.dump(payload, out, separators=(",", ":"), ensure_ascii=False)
                    out.flush()
                    os.fsync(out.fileno())
            if receipt_path.exists():
                raw, result = self._read_result(output_path, request.request_id)
                expected = {
                    "input_sha256": payload["input_sha256"],
                    "output_sha256": hashlib.sha256(raw).hexdigest(),
                }
                if json.loads(receipt_path.read_text()) != expected:
                    raise SimulatorError("Simulator result receipt mismatch")
                return result
            if output_path.exists():
                raise SimulatorError("Simulator result is incomplete or uncertain")
            env = {"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"}
            process = await asyncio.create_subprocess_exec(
                *self.argv,
                str(input_path),
                str(output_path),
                cwd=job,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
                pass_fds=(lock.fileno(),),
            )
            try:
                await asyncio.wait_for(process.wait(), self.timeout_seconds)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
                raise
            if process.returncode != 0:
                raise SimulatorError(f"Simulator exited with status {process.returncode}")
            raw, result = self._read_result(output_path, request.request_id)
            receipt = {
                "input_sha256": payload["input_sha256"],
                "output_sha256": hashlib.sha256(raw).hexdigest(),
            }
            fd = os.open(receipt_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump(receipt, out, separators=(",", ":"))
                out.flush()
                os.fsync(out.fileno())
            return result

    @staticmethod
    def _read_result(path: Path, request_id: str) -> tuple[bytes, SimulationOutput]:
        try:
            if path.is_symlink() or path.stat().st_size > 10_000_000:
                raise SimulatorError("Invalid simulator output file")
            raw = path.read_bytes()
            result = SimulationOutput.model_validate_json(raw)
        except (OSError, ValueError) as exc:
            raise SimulatorError("Invalid simulator output") from exc
        if result.request_id != request_id:
            raise SimulatorError("Simulator request ID mismatch")
        return raw, result


def new_input(control_loop_id: str, scenario_id: str, parameters: PID,
              parameter_version: str, context: dict[str, JsonValue] | None = None) -> SimulationInput:
    return SimulationInput(
        request_id=uid(),
        control_loop_id=control_loop_id,
        scenario_id=scenario_id,
        parameters=parameters,
        parameter_version=parameter_version,
        context=context or {},
    )
