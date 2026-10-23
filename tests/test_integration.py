import json
import sys
from datetime import datetime, timedelta, timezone

import pytest

from packages.adapters.integration import ControlSystemIntegration
from packages.adapters.offline_simulator import OfflineProcessSimulator, SimulatorError, new_input
from packages.adapters.telemetry import (
    MappedTelemetryReader,
    MonitorRules,
    PayloadMapping,
    RuleMonitor,
    Signal,
    SignalRule,
    TelemetryFrame,
)
from packages.contracts.agents import ControlOutput
from packages.contracts.common import PID, Bound, Constraints, SourceKind, now
from packages.domain.core import DomainError, guard


def generic_constraints():
    return Constraints(
        parameter_bounds={
            "gain": Bound(min=0.0, max=3.0, max_delta=0.2),
            "offset": Bound(min=-1.0, max=1.0, max_delta=0.1),
            "feedforward": Bound(min=0.0, max=1.0, max_delta=0.05),
            "ramp": Bound(min=0.0, max=10.0, max_delta=1.0),
        },
        equality_tolerance=0.000001,
    )


def proposal():
    return ControlOutput(
        schema_version="1.0",
        decision="PROPOSE",
        delta=PID(gain=0.2, offset=0.0, feedforward=0.0, ramp=0.0),
        candidate_pid=PID(gain=1.2, offset=0.0, feedforward=0.3, ramp=2.0),
        reason="test",
        expected_effect="test",
        risk_notes=[],
    )


async def test_mapping_and_fast_monitoring():
    raw = {
        "meta": {"loop": "pump-a", "timestamp": now(), "revision": "r7"},
        "tuning": {"gain": 1.0, "offset": 0.0, "feedforward": 0.3, "ramp": 2.0},
        "sensor": {"pressure": 32.0, "temperature": 70.0},
    }

    async def fetch(loop):
        assert loop == "pump-a"
        return raw

    reader = MappedTelemetryReader(
        fetch,
        PayloadMapping(
            observed_at="meta.timestamp",
            control_loop_id="meta.loop",
            parameter_version="meta.revision",
            parameters={key: f"tuning.{key}" for key in raw["tuning"]},
            signals={"pressure": "sensor.pressure", "temperature": "sensor.temperature"},
            signal_units={"pressure": "bar", "temperature": "C"},
        ),
    )
    frame = await reader.read_frame("pump-a")
    rules = RuleMonitor(MonitorRules(
        signals={
            "pressure": SignalRule(unit="bar", minimum=29.0, maximum=31.0),
            "temperature": SignalRule(unit="C", maximum=80.0),
        },
        max_age_seconds=30.0,
    ))
    result = rules.check(frame)
    assert result.state == "ABNORMAL"
    assert [(item.signal, item.code) for item in result.issues] == [
        ("pressure", "ABOVE_MAX")
    ]
    assert set(frame.parameters.root) == {"gain", "offset", "feedforward", "ramp"}
    raw["meta"]["timestamp"] = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
    assert rules.check(await reader.read_frame("pump-a")).state == "UNKNOWN"


async def test_monitor_only_system_needs_no_tuning_parameters():
    async def fetch(loop):
        return {"loop": loop, "time": now(), "sensor": {"flow": 12.0}}

    reader = MappedTelemetryReader(fetch, PayloadMapping(
        observed_at="time", control_loop_id="loop",
        signals={"flow": "sensor.flow"}, signal_units={"flow": "m3/h"},
    ))
    frame = await reader.read_frame("line-b")
    assert frame.parameters is None
    result = RuleMonitor(MonitorRules(
        signals={"flow": SignalRule(unit="m3/h", minimum=10.0)},
        max_age_seconds=30.0,
    )).check(frame)
    assert result.state == "NORMAL"


def test_generic_guard_rejects_missing_and_extra_parameters():
    best = PID(gain=1.0, offset=0.0, feedforward=0.3, ramp=2.0)
    assert guard(proposal(), best, [best], generic_constraints()).root["gain"] == 1.2
    invalid = proposal()
    invalid.candidate_pid = PID(gain=1.2, offset=0.0, feedforward=0.3)
    with pytest.raises(DomainError, match="PID_PARAMETER_MISMATCH"):
        guard(invalid, best, [best], generic_constraints())


async def test_offline_simulator_accepts_four_named_parameters(tmp_path):
    program = tmp_path / "simulator.py"
    program.write_text(
        "import json, sys\n"
        "from pathlib import Path\n"
        "request = json.loads(Path(sys.argv[1]).read_text())\n"
        "values = request['parameters']\n"
        "result = {'request_id': request['request_id'], "
        "'objective': abs(values['gain'] - 1.2), "
        "'metrics': {'error': abs(values['gain'] - 1.2)}, "
        "'samples': [{'elapsed_seconds': 0.0, 'signals': {'pressure': 30.0}}]}\n"
        "Path(sys.argv[2]).write_text(json.dumps(result))\n"
    )
    simulator = OfflineProcessSimulator((sys.executable, str(program)), tmp_path / "jobs")
    request = new_input(
        "pump-a", "steady-high",
        PID(gain=1.2, offset=0.0, feedforward=0.3, ramp=2.0), "r7",
    )
    result = await simulator.evaluate(request)
    assert result.objective == 0.0
    assert await simulator.evaluate(request) == result  # idempotent receipt replay
    assert json.loads((tmp_path / "jobs" / request.request_id / "input.json").read_text())[
        "parameters"
    ] == request.parameters.model_dump()
    changed = request.model_copy(update={
        "parameters": PID(gain=1.1, offset=0.0, feedforward=0.3, ramp=2.0)
    })
    with pytest.raises(SimulatorError, match="different input"):
        await simulator.evaluate(changed)


async def test_integration_passes_model_candidate_to_offline_simulator(tmp_path):
    frame = TelemetryFrame(
        observed_at=now(), control_loop_id="pump-a", source_kind=SourceKind.REAL,
        parameter_version="r7",
        parameters=PID(gain=1.0, offset=0.0, feedforward=0.3, ramp=2.0),
        signals={"pressure": Signal(value=32.0, unit="bar")},
    )

    class Reader:
        async def read_frame(self, control_loop_id):
            return frame

    class Simulator:
        async def evaluate(self, request):
            self.request = request
            return request

    simulator = Simulator()
    integration = ControlSystemIntegration(
        Reader(),
        RuleMonitor(MonitorRules(
            signals={"pressure": SignalRule(unit="bar", maximum=31.0)},
            max_age_seconds=30.0,
        )),
        simulator,
        generic_constraints(),
    )
    frozen, state = await integration.check("pump-a")
    assert state.state == "ABNORMAL"
    request = await integration.evaluate_candidate(frozen, proposal(), "steady-high")
    assert request.parameters.root == proposal().candidate_pid.root
    assert request.parameter_version == "r7"
