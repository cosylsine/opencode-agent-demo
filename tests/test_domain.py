import asyncio
import math
import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from packages.adapters.example import ExampleObservationSource
from packages.adapters.live_telemetry import LiveTelemetry
from packages.adapters.storage import SeriesStore, Store
from packages.contracts.agents import ControlOutput
from packages.contracts.common import PID, uid
from packages.contracts.observations import SnapshotQuery
from packages.domain.core import (
    INITIAL,
    REFERENCE,
    DomainError,
    curve,
    digest,
    guard,
    metrics,
    quality,
    reached,
    safe,
    score,
)

START = "2026-09-22T00:00:00Z"


@pytest.mark.parametrize(
    "pid,error,j,secure,target",
    [
        (INITIAL, 0.09927456, 0.23998753, True, False),
        (REFERENCE, 0.04925471, 0.05164120, True, True),
        (PID(kp=0.95, ki=0.2, kd=0.05), 0.16178742, 0.30832906, True, False),
        (PID(kp=0.9, ki=0.2, kd=0.05), None, None, False, False),
    ],
)
def test_reference_vectors(pid, error, j, secure, target):
    points = curve(pid, "improvable", START)
    m = metrics(points)
    if error is not None:
        assert m.steady_state_error == pytest.approx(error, abs=1e-6)
        assert score(m) == pytest.approx(j, abs=1e-6)
    assert safe(points) == secure
    assert reached(m) == target
    if pid == REFERENCE:
        assert m.settling_time_s == 11


def test_hash_vectors():
    assert digest({"a": 1, "b": -0.0}) == digest({"b": 0, "a": 1.0})
    assert digest({"a": 1}) == "015abd7f5cc57a2dd94b7590f04ad8084273905ee33ec5cebeae62276a97f862"
    with pytest.raises(ValueError):
        digest({"a": math.nan})


@pytest.mark.parametrize("bad", [True, "1", math.inf, math.nan])
def test_strict_numbers(bad):
    with pytest.raises(ValidationError):
        PID(kp=bad, ki=0.2, kd=0.05)


def test_quality_and_settling():
    points = curve(REFERENCE, "normal", START)
    points[110] = points[110].model_copy(update={"pressure": 30.2})
    assert metrics(points).settling_time_s is None  # short suffix is not a valid hold
    points[0] = points[0].model_copy(update={"pressure": None, "quality": "MISSING"})
    assert not quality(points).valid
    with pytest.raises(DomainError):
        metrics(points)


def test_no_improvement():
    assert curve(INITIAL, "no_improvement", START) == curve(REFERENCE, "no_improvement", START)
    assert curve(PID(kp=1.2, ki=0.3, kd=0.07), "no_improvement", START) == curve(
        REFERENCE, "no_improvement", START
    )


async def test_live_jsonl_is_appended_and_used_for_snapshot(tmp_path, monkeypatch):
    db = Store(tmp_path)
    series = SeriesStore(db)
    live = LiveTelemetry(tmp_path)
    second = int(time.time())
    # Seed synthetic history without timing the fixture against disk flush latency.
    # Normal sampling after this block still uses real durable writes.
    with monkeypatch.context() as seed:
        seed.setattr("packages.adapters.live_telemetry.os.fsync", lambda fd: None)
        for tick in range(second - 120, second + 1):
            live.append_until(tick)
    await live.start()
    try:
        rows = live.tail(live.path("improvable"), 121)
        assert len(rows) == 121
        assert all(row["sample_kind"] == "LIVE" for row in rows)
        assert live.window_ready("improvable", rows[-1]["timestamp"])
        first_second = rows[-1]["sample_second"]
        with pytest.raises(DomainError) as duplicate:
            await LiveTelemetry(tmp_path).start()
        assert duplicate.value.code == "LOOP_ALREADY_RUNNING"
        await asyncio.sleep(1.2)
        assert live.latest("improvable")["sample_second"] > first_second
        source = ExampleObservationSource(series, live)
        from packages.contracts.common import now

        snapshot = await source.read_snapshot(SnapshotQuery(
            schema_version="1.0", control_loop_id="feedwater-pump-demo",
            as_of=now(), window_seconds=120, request_id=uid(), scenario_id="improvable",
        ))
        points = series.read(snapshot.series)
        recorded = live.window("improvable", snapshot.as_of)
        assert [point.pressure for point in points] == [row["pressure"] for row in recorded]
        assert snapshot.window_end == recorded[-1]["timestamp"]
    finally:
        await live.close()
        db.close()


async def test_new_loop_needs_full_window_and_does_not_backfill_downtime(tmp_path):
    live = LiveTelemetry(tmp_path)
    await live.start()
    try:
        assert len(live.tail(live.path("improvable"))) == 1
        assert not live.window_ready("improvable", live.latest("improvable")["timestamp"])
    finally:
        await live.close()
    previous = live.latest("improvable")["sample_second"]
    live.append_until(previous + 10)
    rows = live.tail(live.path("improvable"))
    assert [row["sample_second"] for row in rows] == [previous, previous + 10]


def test_guard():
    output = ControlOutput(
        schema_version="1.0",
        decision="PROPOSE",
        delta=PID(kp=0.1, ki=0.0, kd=0.0),
        candidate_pid=REFERENCE,
        reason="demo",
        expected_effect="lower error",
        risk_notes=[],
    )
    assert guard(output, INITIAL, [INITIAL]) == REFERENCE
    with pytest.raises(DomainError, match="DUPLICATE"):
        guard(output, INITIAL, [REFERENCE])
    with pytest.raises(DomainError, match="PID_PRECISION"):
        guard(
            output.model_copy(update={"candidate_pid": PID(kp=1.1000001, ki=0.2, kd=0.05)}),
            INITIAL,
            [],
        )


async def test_snapshot_and_file_integrity(tmp_path):
    db = Store(tmp_path)
    series = SeriesStore(db)
    try:
        snapshot = await ExampleObservationSource(series).read_snapshot(
            SnapshotQuery(
                schema_version="1.0",
                control_loop_id="demo",
                as_of="2026-09-22T00:02:00Z",
                window_seconds=120,
                request_id=uid(),
                scenario_id="improvable",
            )
        )
        assert snapshot.window_start == START
        assert snapshot.pid.effective_at == START
        assert snapshot.pid.pid == INITIAL
        assert len(series.read(snapshot.series)) == 121
        (tmp_path / "series" / (snapshot.series.series_ref + ".jsonl")).write_text("corrupt")
        with pytest.raises(DomainError, match="RESULT_INTEGRITY"):
            series.read(snapshot.series)
    finally:
        db.close()


def test_fixtures():
    from packages.contracts.agents import MonitorOutput
    from packages.contracts.api import RunDetail
    from packages.contracts.observations import ObservationSnapshot
    from packages.contracts.trials import TrialRecord

    for name, cls in [
        ("snapshot", ObservationSnapshot),
        ("trial", TrialRecord),
        ("agent", MonitorOutput),
        ("run", RunDetail),
    ]:
        valid = (Path("tests/fixtures/contracts") / (name + ".json")).read_text()
        obj = cls.model_validate_json(valid)
        data = obj.model_dump(mode="json")
        required = next(k for k, v in cls.model_fields.items() if v.is_required())
        del data[required]
        import json

        with pytest.raises(ValidationError):
            cls.model_validate_json(json.dumps(data))
        with pytest.raises(ValidationError):
            cls.model_validate_json(valid[:-1] + ',"unknown":1}')


def test_equality_tolerance_never_expands_delta_bound():
    output = ControlOutput(
        schema_version="1.0",
        decision="PROPOSE",
        delta=PID(kp=0.1, ki=0.0, kd=0.0),
        candidate_pid=PID(kp=1.100001, ki=0.2, kd=0.05),
        reason="test",
        expected_effect="test",
        risk_notes=[],
    )
    with pytest.raises(DomainError, match="PID_BOUNDS"):
        guard(output, INITIAL, [])


@pytest.mark.parametrize(
    "name,model_name",
    [
        ("invalid-snapshot", "ObservationSnapshot"),
        ("invalid-trial", "TrialRecord"),
        ("invalid-agent", "MonitorOutput"),
        ("invalid-run", "RunDetail"),
    ],
)
def test_invalid_contract_fixtures(name, model_name):
    from packages.contracts.agents import MonitorOutput
    from packages.contracts.api import RunDetail
    from packages.contracts.observations import ObservationSnapshot
    from packages.contracts.trials import TrialRecord

    models = {m.__name__: m for m in (ObservationSnapshot, TrialRecord, MonitorOutput, RunDetail)}
    with pytest.raises(ValidationError):
        models[model_name].model_validate_json(
            (Path("tests/fixtures/contracts") / (name + ".json")).read_text()
        )


def test_bad_timestamp_target_freeze_and_missing_quality():
    points = curve(INITIAL, "improvable", START)
    shuffled = points.copy()
    shuffled[10], shuffled[11] = shuffled[11], shuffled[10]
    assert "TIMESTAMP_MISMATCH" in quality(shuffled).reason_codes
    changed = points.copy()
    changed[10] = changed[10].model_copy(update={"target_pressure": 31.0})
    assert "TARGET_MISMATCH" in quality(changed).reason_codes
    frozen = [p.model_copy(update={"pressure": 30.0}) for p in points]
    assert "FROZEN_SIGNAL" in quality(frozen).reason_codes
    with pytest.raises(ValidationError):
        from packages.contracts.observations import SeriesPoint

        SeriesPoint(
            timestamp=START,
            elapsed_seconds=0.0,
            pressure=None,
            target_pressure=30.0,
            quality="GOOD",
        )
