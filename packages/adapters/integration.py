"""Generic, read-only monitoring and offline candidate evaluation facade."""

from packages.adapters.offline_simulator import SimulationInput, Simulator, new_input
from packages.adapters.telemetry import RuleMonitor, TelemetryFrame, TelemetryReader
from packages.contracts.agents import ControlOutput
from packages.contracts.common import PID, Constraints
from packages.domain.core import DomainError, guard


class ControlSystemIntegration:
    def __init__(self, reader: TelemetryReader, monitor: RuleMonitor,
                 simulator: Simulator, constraints: Constraints):
        self.reader = reader
        self.monitor = monitor
        self.simulator = simulator
        self.constraints = constraints

    async def check(self, control_loop_id: str, previous: TelemetryFrame | None = None):
        frame = await self.reader.read_frame(control_loop_id)
        return frame, self.monitor.check(frame, previous)

    async def evaluate_candidate(
        self,
        frame: TelemetryFrame,
        output: ControlOutput,
        scenario_id: str,
        current_best: PID | None = None,
        seen: list[PID] | None = None,
    ):
        """Validate a model proposal, then pass named values to the simulator only."""
        if self.monitor.check(frame).state == "UNKNOWN":
            raise DomainError("DATA_INVALID")
        if frame.parameters is None or frame.parameter_version is None:
            raise DomainError("PARAMETERS_UNAVAILABLE")
        current_best = current_best or frame.parameters
        previously_tested = list(seen or []) + [frame.parameters, current_best]
        candidate = guard(output, current_best, previously_tested, self.constraints)
        request: SimulationInput = new_input(
            frame.control_loop_id,
            scenario_id,
            candidate,
            frame.parameter_version,
        )
        return await self.simulator.evaluate(request)
