import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    state_dir: Path = Path("var/backend")
    catalog_root: Path = Path(__file__).resolve().parents[2]
    local_dev: bool = True
    opencode_url: str = "http://127.0.0.1:14096"
    opencode_password: str = ""
    provider: str = ""
    model: str = ""
    service_token: str = ""
    proxy_token: str = ""
    allowed_origin: str = "http://127.0.0.1:18000"
    max_iterations: int = 5

    @classmethod
    def from_env(cls):
        return cls(
            state_dir=Path(os.getenv("DEMO_STATE_DIR", "var/backend")),
            catalog_root=Path(os.getenv("DEMO_CATALOG_ROOT", str(Path(__file__).resolve().parents[2]))),
            local_dev=os.getenv("DEMO_LOCAL_DEV", "1") == "1",
            opencode_url=os.getenv("DEMO_OPENCODE_URL", "http://127.0.0.1:14096"),
            opencode_password=os.getenv("OPENCODE_SERVER_PASSWORD", ""),
            provider=os.getenv("DEMO_PROVIDER", ""),
            model=os.getenv("DEMO_MODEL", ""),
            service_token=os.getenv("MCP_SERVICE_TOKEN", ""),
            proxy_token=os.getenv("DEMO_PROXY_TOKEN", ""),
            allowed_origin=os.getenv("DEMO_ORIGIN", "http://127.0.0.1:18000"),
            max_iterations=int(os.getenv("DEMO_MAX_ITERATIONS", "5")),
        )

    def validate(self):
        if not 1 <= self.max_iterations <= 5:
            raise ValueError("max_iterations must be 1..5")
        if not self.local_dev and (
            len(self.proxy_token) < 32 or not self.allowed_origin.startswith("https://")
        ):
            raise ValueError("production requires proxy credential and HTTPS origin")
        if not self.opencode_url.startswith(("http://127.0.0.1:", "http://localhost:")):
            raise ValueError("OpenCode must listen on loopback")
