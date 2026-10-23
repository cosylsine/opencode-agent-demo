import hashlib
import secrets
import time
from dataclasses import dataclass

from packages.domain.core import DomainError

TOOLS = {
    "monitor": {"get_recent_window", "calculate_metrics", "detect_state"},
    "control": {"get_tuning_context"},
}


@dataclass
class Scope:
    run_id: str
    request_id: str
    iteration_id: str | None
    role: str
    expires: float


class Tokens:
    def __init__(self):
        self.scopes: dict[str, Scope] = {}

    @staticmethod
    def key(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def issue(self, run_id, request_id, iteration_id, role, ttl=150):
        token = secrets.token_urlsafe(32)
        self.scopes[self.key(token)] = Scope(
            run_id, request_id, iteration_id, role, time.monotonic() + ttl
        )
        return token

    def authorize(self, token, tool):
        scope = self.scopes.get(self.key(token))
        if scope is None or scope.expires < time.monotonic() or tool not in TOOLS[scope.role]:
            raise DomainError("TOOL_FORBIDDEN", status=403)
        return scope

    def revoke(self, token):
        self.scopes.pop(self.key(token), None)

    def revoke_run(self, run_id):
        self.scopes = {k: v for k, v in self.scopes.items() if v.run_id != run_id}
