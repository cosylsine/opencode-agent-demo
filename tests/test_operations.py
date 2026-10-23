import pytest

from packages.adapters.storage import Store
from scripts.backup import backup, restore
from tests.test_workflow import execute


async def test_backup_restore(tmp_path):
    source = tmp_path / "source"
    db, engine, evaluator, fake, run = await execute(source)
    await evaluator.close()
    db.close()
    (source / "telemetry").mkdir()
    (source / "telemetry" / "improvable.jsonl").write_text('{"sample":"demo"}\n')
    target = tmp_path / "backup"
    restored = tmp_path / "restored"
    backup(source, target)
    restore(target, restored)
    assert (restored / "telemetry" / "improvable.jsonl").read_text() == (
        '{"sample":"demo"}\n'
    )
    reopened = Store(restored)
    try:
        assert reopened.get(run.run_id).status == "SUCCEEDED"
        from packages.adapters.storage import SeriesStore

        series = SeriesStore(reopened)
        assert len(series.read(series.resolve(run.observation_series_ref))) == 121
    finally:
        reopened.close()
    (target / "demo.db").write_bytes(b"bad")
    with pytest.raises(ValueError, match="hash"):
        restore(target, tmp_path / "corrupt")


def test_backup_requires_stopped_service(tmp_path):
    db = Store(tmp_path / "source")
    try:
        with pytest.raises(BlockingIOError):
            backup(tmp_path / "source", tmp_path / "backup")
    finally:
        db.close()


async def test_backup_requires_stopped_virtual_loop(tmp_path):
    from packages.adapters.live_telemetry import LiveTelemetry

    source = tmp_path / "source"
    Store(source).close()
    loop = LiveTelemetry(source)
    await loop.start()
    try:
        with pytest.raises(BlockingIOError):
            backup(source, tmp_path / "backup")
    finally:
        await loop.close()


async def test_recovery_corrupt_manifest_and_file(tmp_path):
    from packages.adapters.example import ExampleEvaluator
    from packages.adapters.storage import jobs

    db, engine, evaluator, fake, run = await execute(tmp_path, steps=5)
    await evaluator.close()
    trial = db.job(trial_id=run.baseline_trial_id)
    path = tmp_path / "jobs" / trial.trial_id / "result.jsonl"
    path.write_text(path.read_text().replace("30.4", "30.3"))
    trial.status = "RUNNING"
    trial.series = None
    trial.finished_at = None
    with db.engine.begin() as conn:
        conn.execute(
            jobs.update()
            .where(jobs.c.id == trial.trial_id)
            .values(data=trial.model_dump(mode="json"))
        )
    evaluator = ExampleEvaluator(db, engine.series)
    await evaluator.submit(trial.request)
    import asyncio

    await asyncio.gather(*evaluator.tasks.values())
    recovered = await evaluator.get(trial.trial_id)
    assert recovered.status == "FAILED"
    assert recovered.error.code == "RESULT_INTEGRITY_ERROR"
    await evaluator.close()
    db.close()


async def test_partial_result_retries_same_job_once(tmp_path):
    import asyncio

    from packages.adapters.example import ExampleEvaluator
    from packages.adapters.storage import jobs

    db, engine, evaluator, fake, run = await execute(tmp_path, steps=5)
    await evaluator.close()
    trial = db.job(trial_id=run.baseline_trial_id)
    directory = tmp_path / "jobs" / trial.trial_id
    (directory / "result.jsonl").unlink()
    (directory / "result.tmp").write_text("{incomplete")
    trial.status = "RUNNING"
    trial.series = None
    trial.finished_at = None
    with db.engine.begin() as conn:
        conn.execute(
            jobs.update()
            .where(jobs.c.id == trial.trial_id)
            .values(data=trial.model_dump(mode="json"))
        )
    evaluator = ExampleEvaluator(db, engine.series)
    await evaluator.submit(trial.request)
    await asyncio.gather(*evaluator.tasks.values())
    assert (await evaluator.get(trial.trial_id)).status == "COMPLETED"
    assert (directory / "attempt.json").read_text() == "2"
    await evaluator.close()
    db.close()


async def test_unproven_child_lock_never_reexecutes(tmp_path):
    import asyncio
    import fcntl

    from packages.adapters.example import ExampleEvaluator
    from packages.adapters.storage import jobs

    db, engine, evaluator, fake, run = await execute(tmp_path, steps=5)
    await evaluator.close()
    trial = db.job(trial_id=run.baseline_trial_id)
    trial.status = "RUNNING"
    trial.series = None
    trial.finished_at = None
    with db.engine.begin() as conn:
        conn.execute(
            jobs.update()
            .where(jobs.c.id == trial.trial_id)
            .values(data=trial.model_dump(mode="json"))
        )
    directory = tmp_path / "jobs" / trial.trial_id
    lock = (directory / "child.lock").open("a")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        evaluator = ExampleEvaluator(db, engine.series)
        await evaluator.submit(trial.request)
        await asyncio.gather(*evaluator.tasks.values())
        assert (await evaluator.get(trial.trial_id)).error.code == "RESULT_UNKNOWN"
        assert (directory / "attempt.json").read_text() == "1"
    finally:
        lock.close()
        await evaluator.close()
        db.close()
