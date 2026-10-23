"""Explicit account setup for legacy business regression tests; no production auth bypass."""
from fastapi.testclient import TestClient

PASSWORD = "regression-test-password"


class AuthenticatedClient(TestClient):
    def __enter__(self):
        client = super().__enter__()
        platform = self.app.state.platform
        if not platform.initialized():
            platform.initialize_admin("regression-admin", "回归管理员", PASSWORD)
        admin_id = platform.admin_id()
        account = platform.get_user(admin_id)
        token, _, _ = platform.login(account["username"], PASSWORD)
        self.cookies.set("office_session", token)
        self.user_id = admin_id
        return client
