import asyncio

from packages.contracts.agents import ControlOutput, MonitorOutput
from packages.contracts.common import PID
from packages.domain.core import DomainError


class FakeClient:
    """Explicit test injection only; never imported by the service."""

    def __init__(self, config=None, controls=None, monitor=None, delay=0):
        self.ready = True
        self.version = "FAKE_TEST_ONLY"
        self.error_code = None
        self.controls = list(controls or [])
        self.monitor = monitor
        self.calls = []
        self.sessions = {}
        self.delay = delay

    async def probe(self):
        pass

    async def close(self):
        pass

    async def st_review(self, request, on_session, correction=""):
        from tests.st_fixtures import result_for
        self.calls.append(("st-code", request.model_dump()))
        session = await self.create_session("ST test")
        on_session(session)
        await asyncio.sleep(self.delay)
        return result_for(request)

    async def chat(self, history, message, run_context=None):
        self.calls.append(("reception", message))
        return "Fake reception: " + message

    async def create_session(self, title):
        key = "ses_" + str(len(self.sessions))
        self.sessions[key] = None
        return key

    async def abort(self, session):
        return True

    async def recover(self, session, schema, role, **kwargs):
        if self.sessions.get(session) is not None:
            return self.sessions[session]
        raise DomainError("RESULT_UNKNOWN")

    async def control_context(self, token, workflow):
        return workflow.context_reader()["data"]

    async def prompt(self, session, role, payload, schema, correction="", *, workflow=None):
        self.calls.append((role, payload))
        await asyncio.sleep(self.delay)
        if role == "monitor":
            assessment = payload["rule_assessment"]["assessment"]
            output = self.monitor or MonitorOutput(
                schema_version="1.0",
                assessment=assessment,
                confidence=0.9,
                needs_tuning=assessment in ("STEADY_HIGH", "STEADY_LOW"),
                manual_review_required=False,
                evidence=[],
                reason="Fake test assessment",
                warnings=[],
            )
        else:
            if self.controls:
                output = self.controls.pop(0)
                if isinstance(output, Exception):
                    raise output
            elif payload.get("source_kind") == "SIMULATION":
                # The real Control reads business data through MCP. This fake
                # only models a first proposal followed by abstention.
                from packages.simulation.plant import BASE_PID
                best = BASE_PID
                if sum(role == "control" for role, _ in self.calls) > 1:
                    output = ControlOutput(schema_version="1.0", decision="ABSTAIN", delta=None,
                                           candidate_pid=None, reason="示例候选已经完成", expected_effect="维持当前最佳参数", risk_notes=[])
                else:
                    target = {"kp": 0.8, "ki": 0.06, "kd": 0.0}
                    output = ControlOutput(schema_version="1.0", decision="PROPOSE",
                                           delta=PID(**{key: target[key]-best[key] for key in target}),
                                           candidate_pid=PID(**target), reason="改善示例给水响应",
                                           expected_effect="降低四工况综合误差", risk_notes=[])
            else:
                output = ControlOutput(
                    schema_version="1.0",
                    decision="PROPOSE",
                    delta=PID(kp=0.1, ki=0.0, kd=0.0),
                    candidate_pid=PID(kp=1.1, ki=0.2, kd=0.05),
                    reason="Fake test proposal",
                    expected_effect="artificial q decreases",
                    risk_notes=[],
                )
        self.sessions[session] = output
        return output


def proposal(kp, delta):
    return ControlOutput(
        schema_version="1.0",
        decision="PROPOSE",
        delta=PID(kp=delta, ki=0.0, kd=0.0),
        candidate_pid=PID(kp=kp, ki=0.2, kd=0.05),
        reason="test",
        expected_effect="test",
        risk_notes=[],
    )
