import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from packages.adapters.example import ExampleObservationSource
from packages.adapters.storage import SeriesStore, Store
from packages.contracts.agents import MonitorOutput
from packages.contracts.api import RunDetail
from packages.contracts.common import SourceKind, now, uid
from packages.contracts.observations import SnapshotQuery
from packages.contracts.trials import TrialRecord, TrialRequest
from packages.domain.core import INITIAL, request_hash


async def main():
    dest = Path("tests/fixtures/contracts")
    with tempfile.TemporaryDirectory() as tmp:
        db = Store(Path(tmp))
        snapshot = await ExampleObservationSource(SeriesStore(db)).read_snapshot(
            SnapshotQuery(
                schema_version="1.0",
                control_loop_id="demo",
                as_of="2026-09-22T00:02:00Z",
                window_seconds=120,
                request_id=uid(),
                scenario_id="improvable",
            )
        )
        run_id = uid()
        request = TrialRequest(
            schema_version="1.0",
            run_id=run_id,
            iteration_id=None,
            candidate_id=None,
            control_loop_id="demo",
            trace_id=uid(),
            purpose="BASELINE",
            idempotency_key="baseline",
            candidate_pid=INITIAL,
            evaluation_context_ref=uid(),
            evaluation_context_hash="a" * 64,
            request_hash="",
            timeout_seconds=10,
        )
        request.request_hash = request_hash(request)
        trial = TrialRecord(
            schema_version="1.0",
            trial_id=uid(),
            request=request,
            status="COMPLETED",
            source_kind=SourceKind.EXAMPLE,
            adapter_id="example-evaluator",
            adapter_version="example-v2",
            created_at=now(),
            started_at=now(),
            finished_at=now(),
            series=snapshot.series,
            error=None,
        )
        agent = MonitorOutput(
            schema_version="1.0",
            assessment="STEADY_HIGH",
            confidence=0.9,
            needs_tuning=True,
            manual_review_required=False,
            evidence=[],
            reason="示例偏高",
            warnings=[],
        )
        run = RunDetail(
            schema_version="1.0",
            run_id=run_id,
            control_loop_id="demo",
            scenario_id="improvable",
            status="CREATED",
            state_version=0,
            source_kind=SourceKind.EXAMPLE,
            created_at=now(),
            finished_at=None,
            actual_pid=None,
            best_pid=None,
            best_metrics=None,
            best_score=None,
            observation_series_ref=None,
            baseline_series_ref=None,
            monitor_output=None,
            iterations=[],
            stop_reason=None,
            error=None,
        )
        for name, value in [
            ("snapshot", snapshot),
            ("trial", trial),
            ("agent", agent),
            ("run", run),
        ]:
            (dest / (name + ".json")).write_text(value.model_dump_json(indent=2))
        db.close()


asyncio.run(main())
