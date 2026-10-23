"""Generate schemas and, once API is available, OpenAPI. Do not hand edit output."""

import inspect
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from packages.contracts import agents, api, observations, office, st_code, trials, tuning
from packages.contracts.common import Envelope

root = Path(__file__).resolve().parents[1] / "docs/api"
root.mkdir(parents=True, exist_ok=True)
for module in (agents, api, observations, trials, tuning, office, st_code):
    for name, cls in inspect.getmembers(module, inspect.isclass):
        if cls.__module__ == module.__name__ and issubclass(cls, Envelope):
            (root / (name + ".schema.json")).write_text(
                json.dumps(cls.model_json_schema(), indent=2) + "\n"
            )
try:
    from apps.orchestrator.main import create_app
except ImportError:
    pass
else:
    (root / "openapi.json").write_text(json.dumps(create_app().openapi(), indent=2) + "\n")
