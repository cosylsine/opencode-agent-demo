import threading

import pytest

from packages.adapters.live_telemetry import LiveTelemetry
from packages.domain.core import SCENARIOS


def test_scenarios_flush_concurrently_and_all_complete_before_return(tmp_path, monkeypatch):
    live = LiveTelemetry(tmp_path)
    barrier = threading.Barrier(len(SCENARIOS))
    flushed = []

    def flush(fd):
        # Sequential writes cannot cross this barrier. No timing performance
        # assertion is needed; all scenario writes must be in flight together.
        barrier.wait(timeout=5)
        flushed.append(fd)

    monkeypatch.setattr("packages.adapters.live_telemetry.os.fsync", flush)
    live.append_until(1000)
    assert len(flushed) == len(SCENARIOS)
    for scenario in SCENARIOS:
        assert live.latest(scenario)["sample_second"] == 1000
    live.append_until(1000)
    assert len(flushed) == len(SCENARIOS)
    live.append_until(1010)
    for scenario in SCENARIOS:
        assert [row["sample_second"] for row in live.tail(live.path(scenario))] == [1000, 1010]


def test_flush_failure_is_not_silently_accepted(tmp_path, monkeypatch):
    def failed(fd):
        raise OSError("disk unavailable")

    monkeypatch.setattr("packages.adapters.live_telemetry.os.fsync", failed)
    with pytest.raises(OSError, match="disk unavailable"):
        LiveTelemetry(tmp_path).append_until(1000)
