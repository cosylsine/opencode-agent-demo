import fcntl
import hashlib
import json
import os
from pathlib import Path

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    event,
    select,
    text,
)

from packages.contracts.common import now, uid
from packages.contracts.internal import AgentAttempt, RunRecord
from packages.contracts.observations import SeriesArtifact, SeriesPoint
from packages.contracts.trials import TrialRecord
from packages.contracts.tuning import TuningRun
from packages.domain.core import DomainError
from packages.domain.states import TERMINAL, transition

metadata = MetaData()
runs = Table(
    "run",
    metadata,
    Column("id", String, primary_key=True),
    Column("loop_id", String, nullable=False),
    Column("active", Boolean, nullable=False),
    Column("version", Integer, nullable=False),
    Column("data", JSON, nullable=False),
)
tuning_runs = Table(
    "tuning_run",
    metadata,
    Column("id", String, primary_key=True),
    Column("active", Boolean, nullable=False),
    Column("version", Integer, nullable=False),
    Column("data", JSON, nullable=False),
)
st_runs = Table(
    "st_code_run", metadata,
    Column("id", String, primary_key=True),
    Column("owner_id", String, nullable=False, index=True),
    Column("status", String, nullable=False, index=True),
    Column("data", JSON, nullable=False),
)
iterations = Table(
    "iteration",
    metadata,
    Column("id", String, primary_key=True),
    Column("run_id", String, nullable=False),
    Column("data", JSON, nullable=False),
)
jobs = Table(
    "trial_job",
    metadata,
    Column("id", String, primary_key=True),
    Column("key", String, unique=True, nullable=False),
    Column("data", JSON, nullable=False),
)
attempts = Table(
    "agent_request",
    metadata,
    Column("id", String, primary_key=True),
    Column("run_id", String, nullable=False),
    Column("data", JSON, nullable=False),
)
events = Table(
    "audit_event",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_id", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("event", String, nullable=False),
    Column("detail", JSON, nullable=False),
)
artifacts = Table(
    "series_artifact",
    metadata,
    Column("id", String, primary_key=True),
    Column("owner_id", String, nullable=False),
    Column("data", JSON, nullable=False),
)
idempotency = Table(
    "api_idempotency",
    metadata,
    Column("key", String, primary_key=True),
    Column("hash", String, nullable=False),
    Column("data", JSON, nullable=False),
)
triggers = Table(
    "trigger_event",
    metadata,
    Column("id", String, primary_key=True),
    Column("run_id", String, nullable=False),
    Column("data", JSON, nullable=False),
)
settings = Table(
    "settings",
    metadata,
    Column("key", String, primary_key=True),
    Column("data", JSON, nullable=False),
)
chat_sessions = Table(
    "chat_session",
    metadata,
    Column("id", String, primary_key=True),
    Column("user_id", String, nullable=False),
    Column("title", String, nullable=False),
    Column("created_at", String, nullable=False),
)
chat_messages = Table(
    "chat_message",
    metadata,
    Column("id", String, primary_key=True),
    Column("session_id", String, nullable=False),
    Column("role", String, nullable=False),
    Column("content", String, nullable=False),
    Column("created_at", String, nullable=False),
    Column("run_id", String),
    Column("request_key", String),
    Column("request_hash", String),
    Column("status", String, nullable=False),
)


class Store:
    def __init__(self, root: Path, lock: bool = True):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.lock = (self.root / "process.lock").open("a") if lock else None
        if self.lock:
            try:
                fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self.lock.close()
                raise DomainError("PROCESS_ALREADY_RUNNING") from None
        self.engine = create_engine(
            "sqlite:///" + str(self.root / "demo.db"),
            connect_args={"check_same_thread": False, "timeout": 10},
        )

        @event.listens_for(self.engine, "connect")
        def configure(dbapi, _):
            dbapi.execute("PRAGMA journal_mode=WAL")
            dbapi.execute("PRAGMA foreign_keys=ON")
            dbapi.execute("PRAGMA synchronous=FULL")

        self.migrate()

    def migrate(self):
        from alembic import command
        from alembic.config import Config

        cfg = Config()
        cfg.set_main_option("script_location", str(Path(__file__).parents[2] / "migrations"))
        cfg.attributes["engine"] = self.engine
        command.upgrade(cfg, "head")

    def close(self):
        self.engine.dispose()
        if self.lock:
            self.lock.close()

    def get_tuning(self, run_id: str, conn=None) -> TuningRun:
        if conn is None:
            with self.engine.connect() as db:
                return self.get_tuning(run_id, db)
        value = conn.execute(select(tuning_runs.c.data).where(tuning_runs.c.id == run_id)).scalar_one_or_none()
        if value is None:
            raise DomainError("NOT_FOUND", status=404)
        return TuningRun.model_validate_json(json.dumps(value))

    def active_tuning(self, conn=None) -> TuningRun | None:
        if conn is None:
            with self.engine.connect() as db:
                return self.active_tuning(db)
        value = conn.execute(select(tuning_runs.c.data).where(tuning_runs.c.active)).scalar_one_or_none()
        return TuningRun.model_validate_json(json.dumps(value)) if value else None

    def list_tuning(self, owner_id=None) -> list[TuningRun]:
        with self.engine.connect() as db:
            query = select(tuning_runs.c.data)
            if owner_id is not None:
                from packages.adapters.platform_store import owners
                query = query.where(tuning_runs.c.id.in_(select(owners.c.id).where(owners.c.owner_id == owner_id)))
            values = db.execute(query).scalars().all()
        return sorted((TuningRun.model_validate_json(json.dumps(v)) for v in values),
                      key=lambda r: r.created_at, reverse=True)[:100]

    def add_tuning(self, run: TuningRun, conn=None):
        if conn is None:
            with self.engine.begin() as db:
                return self.add_tuning(run, db)
        if self.active_tuning(conn):
            raise DomainError("SCENARIO_BUSY", "已有 B 回路调优正在运行")
        conn.execute(tuning_runs.insert().values(id=run.run_id, active=True, version=0, data=run.model_dump(mode="json")))

    def save_tuning(self, run: TuningRun, conn=None):
        if conn is None:
            with self.engine.begin() as db:
                return self.save_tuning(run, db)
        old = run.state_version
        run.state_version += 1
        run.updated_at = now()
        active = run.status not in {"COMPLETED", "NO_SAFE_CANDIDATE", "AGENT_ABSTAINED", "FAILED", "CANCELLED"}
        result = conn.execute(tuning_runs.update().where(tuning_runs.c.id == run.run_id, tuning_runs.c.version == old).values(
            active=active, version=run.state_version, data=run.model_dump(mode="json"),
        ))
        if result.rowcount != 1:
            raise DomainError("RUN_STATE_CONFLICT")

    def get(self, run_id, conn=None) -> RunRecord:
        if conn is None:
            with self.engine.connect() as db:
                return self.get(run_id, db)
        value = conn.execute(select(runs.c.data).where(runs.c.id == run_id)).scalar_one_or_none()
        if value is None:
            raise DomainError("NOT_FOUND", status=404)
        return RunRecord.model_validate_json(json.dumps(value))

    def active(self, conn=None):
        if conn is None:
            with self.engine.connect() as db:
                return self.active(db)
        return [
            RunRecord.model_validate_json(json.dumps(x))
            for x in conn.execute(select(runs.c.data).where(runs.c.active)).scalars()
        ]

    def add(self, run: RunRecord, conn):
        conn.execute(
            runs.insert().values(
                id=run.run_id,
                loop_id=run.control_loop_id,
                active=True,
                version=run.state_version,
                data=run.model_dump(mode="json"),
            )
        )
        self.audit(run.run_id, "CREATED", {}, conn)

    def audit(self, run_id: str, name: str, detail: dict, conn=None):
        if conn is None:
            with self.engine.begin() as db:
                return self.audit(run_id, name, detail, db)
        conn.execute(
            events.insert().values(run_id=run_id, created_at=now(), event=name, detail=detail)
        )

    def save(self, run: RunRecord, old_version: int, name: str, detail=None, conn=None):
        if conn is None:
            with self.engine.begin() as db:
                return self.save(run, old_version, name, detail, db)
        previous = self.get(run.run_id, conn)
        if previous.state_version != old_version:
            raise DomainError("RUN_STATE_CONFLICT")
        if previous.status in TERMINAL:
            raise DomainError("RUN_STATE_CONFLICT")
        if previous.cancel_requested and run.status != "CANCELLED":
            raise DomainError("CANCEL_REQUESTED")
        if previous.status != run.status:
            transition(previous.status, run.status)
        run.state_version = old_version + 1
        RunRecord.model_validate_json(run.model_dump_json())
        result = conn.execute(
            runs.update()
            .where(runs.c.id == run.run_id, runs.c.version == old_version)
            .values(
                version=run.state_version,
                active=run.status not in TERMINAL,
                data=run.model_dump(mode="json"),
            )
        )
        if result.rowcount != 1:
            raise DomainError("RUN_STATE_CONFLICT")
        for item in run.iterations:
            conn.execute(
                text(
                    "INSERT INTO iteration (id,run_id,data) VALUES (:id,:run,:data) "
                    "ON CONFLICT(id) DO UPDATE SET data=excluded.data"
                ),
                {"id": item.iteration_id, "run": run.run_id, "data": item.model_dump_json()},
            )
        self.audit(run.run_id, name, detail or {}, conn)

    def cancel_mark(self, run_id, conn):
        run = self.get(run_id, conn)
        if run.status not in TERMINAL and not run.cancel_requested:
            run.cancel_requested = True
            self.save(run, run.state_version, "CANCEL_REQUESTED", conn=conn)
        return run

    def save_attempt(self, value: AgentAttempt, conn=None):
        if conn is None:
            with self.engine.begin() as db:
                return self.save_attempt(value, db)
        conn.execute(
            text(
                "INSERT INTO agent_request(id,run_id,data) VALUES(:id,:run,:data) "
                "ON CONFLICT(id) DO UPDATE SET data=excluded.data"
            ),
            {"id": value.request_id, "run": value.run_id, "data": value.model_dump_json()},
        )

    def attempt_list(self, run_id, role, iteration_id=None):
        with self.engine.connect() as db:
            values = db.execute(
                select(attempts.c.data).where(attempts.c.run_id == run_id)
            ).scalars()
            parsed = [AgentAttempt.model_validate_json(json.dumps(x)) for x in values]
        return [x for x in parsed if x.role == role and x.iteration_id == iteration_id]

    def save_job(self, value: TrialRecord):
        with self.engine.begin() as db:
            existing = db.execute(
                select(jobs.c.data).where(jobs.c.id == value.trial_id)
            ).scalar_one_or_none()
            if existing and existing["status"] in ("COMPLETED", "FAILED", "CANCELLED"):
                if existing != value.model_dump(mode="json"):
                    raise DomainError("TRIAL_STATE_CONFLICT")
                return
            db.execute(
                text(
                    "INSERT INTO trial_job(id,key,data) VALUES(:id,:key,:data) "
                    "ON CONFLICT(id) DO UPDATE SET data=excluded.data"
                ),
                {
                    "id": value.trial_id,
                    "key": value.request.idempotency_key,
                    "data": value.model_dump_json(),
                },
            )

    def job(self, trial_id=None, key=None) -> TrialRecord:
        with self.engine.connect() as db:
            data = db.execute(
                select(jobs.c.data).where(jobs.c.id == trial_id if trial_id else jobs.c.key == key)
            ).scalar_one_or_none()
        if data is None:
            raise DomainError("NOT_FOUND", status=404)
        return TrialRecord.model_validate_json(json.dumps(data))


class SeriesStore:
    def __init__(self, db: Store):
        self.db = db
        self.root = db.root / "series"
        self.root.mkdir(exist_ok=True, mode=0o700)

    def write(self, points: list[SeriesPoint], owner_id: str, ref: str | None = None):
        ref = ref or uid()
        path = self.root / (ref + ".jsonl")
        raw = "".join(p.model_dump_json() + "\n" for p in points).encode()
        artifact = SeriesArtifact(
            series_ref=ref,
            sha256=hashlib.sha256(raw).hexdigest(),
            sample_count=len(points),
            start_at=points[0].timestamp,
            end_at=points[-1].timestamp,
            duration_seconds=points[-1].elapsed_seconds,
        )
        if path.exists() and path.read_bytes() != raw:
            raise DomainError("RESULT_INTEGRITY_ERROR")
        atomic_write(path, raw)
        with self.db.engine.begin() as conn:
            existing = conn.execute(
                select(artifacts.c.data).where(artifacts.c.id == ref)
            ).scalar_one_or_none()
            if existing is None:
                conn.execute(
                    artifacts.insert().values(
                        id=ref, owner_id=owner_id, data=artifact.model_dump(mode="json")
                    )
                )
            elif existing != artifact.model_dump(mode="json"):
                raise DomainError("RESULT_INTEGRITY_ERROR")
        return artifact

    def read(self, artifact: SeriesArtifact):
        try:
            raw = (self.root / (artifact.series_ref + ".jsonl")).read_bytes()
            if len(raw) > 2_000_000 or hashlib.sha256(raw).hexdigest() != artifact.sha256:
                raise ValueError("hash or size")
            points = [SeriesPoint.model_validate_json(line) for line in raw.splitlines()]
            if (
                len(points) != artifact.sample_count
                or points[0].timestamp != artifact.start_at
                or points[-1].timestamp != artifact.end_at
            ):
                raise ValueError("metadata")
            return points
        except (ValueError, OSError, IndexError) as exc:
            raise DomainError("RESULT_INTEGRITY_ERROR") from exc

    def resolve(self, ref):
        with self.db.engine.connect() as conn:
            data = conn.execute(
                select(artifacts.c.data).where(artifacts.c.id == ref)
            ).scalar_one_or_none()
        if data is None:
            raise DomainError("NOT_FOUND", status=404)
        return SeriesArtifact.model_validate_json(json.dumps(data))


def atomic_write(path: Path, raw: bytes):
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("wb") as out:
        os.chmod(temp, 0o600)
        out.write(raw)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temp, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
