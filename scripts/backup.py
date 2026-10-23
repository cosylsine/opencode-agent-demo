"""Offline consistent backup/restore. Stop the backend first; never overwrite a target."""

import argparse
import fcntl
import hashlib
import json
import shutil
import sqlite3
import tempfile
from contextlib import ExitStack
from pathlib import Path


def manifest(root: Path):
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.name != "backup-manifest.json"
    }


def backup(source: Path, target: Path):
    if target.exists():
        raise ValueError("Backup destination already exists")
    with ExitStack() as stack:
        backend_lock = stack.enter_context((source / "process.lock").open("a"))
        fcntl.flock(backend_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        sampler_lock_path = source / "telemetry" / "sampler.lock"
        if sampler_lock_path.exists():
            sampler_lock = stack.enter_context(sampler_lock_path.open("a+"))
            fcntl.flock(sampler_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        target.mkdir(parents=True, mode=0o700)
        try:
            with sqlite3.connect(
                "file:" + str((source / "demo.db").resolve()) + "?mode=ro", uri=True
            ) as src:
                with sqlite3.connect(target / "demo.db") as dst:
                    src.backup(dst)
                    if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise ValueError("Database integrity failure")
            for name in ("series", "jobs", "telemetry", "office"):
                if (source / name).exists():
                    shutil.copytree(source / name, target / name)
            (target / "backup-manifest.json").write_text(json.dumps(manifest(target), indent=2))
        except Exception:
            shutil.rmtree(target)
            raise


def restore(source: Path, target: Path):
    if target.exists():
        raise ValueError("Restore only to a new, empty state directory")
    expected = json.loads((source / "backup-manifest.json").read_text())
    if manifest(source) != expected:
        raise ValueError("Backup hash mismatch")
    if any(p.is_symlink() for p in source.rglob("*")):
        raise ValueError("Backup may not contain symlinks")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
        staging = Path(tmp) / "state"
        shutil.copytree(source, staging)
        (staging / "backup-manifest.json").unlink()
        with sqlite3.connect(staging / "demo.db") as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Database integrity failure")
            for (raw,) in db.execute("SELECT data FROM series_artifact"):
                item = json.loads(raw)
                from uuid import UUID

                UUID(item["series_ref"])
                path = staging / "series" / (item["series_ref"] + ".jsonl")
                if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                    raise ValueError("Stored artifact hash mismatch")
        staging.rename(target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["backup", "restore"])
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    (backup if args.action == "backup" else restore)(args.source, args.target)
    print(args.action + " verified: " + str(args.target))


if __name__ == "__main__":
    main()
