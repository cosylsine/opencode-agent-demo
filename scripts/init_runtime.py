"""Create fresh project secrets without reading existing credentials or overwriting files."""

import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
directory = ROOT / "deploy/runtime"
directory.mkdir(parents=True, exist_ok=True, mode=0o700)
os.chmod(directory, 0o700)
path = directory / "runtime.env"
text = (ROOT / "deploy/runtime.env.example").read_text()
for key in ("OPENCODE_SERVER_PASSWORD", "MCP_SERVICE_TOKEN", "DEMO_PROXY_TOKEN"):
    text = text.replace(key + "=\n", key + "=" + secrets.token_urlsafe(40) + "\n")
fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
with os.fdopen(fd, "w") as out:
    out.write(text)
print("Created private runtime.env; configure provider/model before starting OpenCode.")
