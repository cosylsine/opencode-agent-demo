"""Fixed entry point; accepts only a contract manifest, writes only in its job cwd."""

import json
from pathlib import Path

from packages.contracts.trials import EvaluationContext, TrialRequest
from packages.domain.core import context_hash, curve, request_hash


def main():
    manifest = json.loads(Path("manifest.json").read_text())
    request = TrialRequest.model_validate_json(json.dumps(manifest["request"]))
    context = EvaluationContext.model_validate_json(json.dumps(manifest["context"]))
    if (
        request_hash(request) != request.request_hash
        or context_hash(context) != context.context_hash
    ):
        raise ValueError("manifest hash mismatch")
    if context.source_kind != "EXAMPLE" or context.program_version != "example-v2":
        raise ValueError("unsupported program")
    points = curve(request.candidate_pid, context.scenario_id, context.start_at)
    with Path("result.tmp").open("w") as out:
        for point in points:
            out.write(point.model_dump_json() + "\n")
        out.flush()
        import os

        os.fsync(out.fileno())


if __name__ == "__main__":
    main()
