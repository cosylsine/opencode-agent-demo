"""Export committed product sources and intranet templates, without offline binaries."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

TEMPLATES = "deploy/intranet"
MANIFEST = "SOURCE-EXPORT.json"


def git(repository: Path, *arguments: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout


def archive_commit(repository: Path, commit: str, stream) -> None:
    subprocess.run(
        ["git", "-C", str(repository), "archive", "--format=tar", commit],
        check=True, stdout=stream, stderr=subprocess.PIPE,
    )
    stream.seek(0)


def export_plan(archive: tarfile.TarFile) -> list[tuple[tarfile.TarInfo, Path]]:
    """Validate the entire archive before creating any output files."""
    plan = []
    destinations = set()
    has_templates = False
    for member in archive:
        name = member.name.rstrip("/") if member.isdir() else member.name
        parts = name.split("/")
        if (
            not name or "\\" in name or PurePosixPath(name).is_absolute()
            or any(part in {"", ".", "..", ".git"} for part in parts)
        ):
            raise ValueError(f"Unsafe Git archive path: {member.name!r}")
        if member.isdir():
            continue
        if not member.isfile():
            raise ValueError(f"Unsupported archive entry (links are forbidden): {name!r}")
        if name.startswith(TEMPLATES + "/"):
            relative = Path(*parts[2:])
            if relative.parts[0] in {"source", MANIFEST}:
                raise ValueError(f"Deployment template uses a reserved output path: {name!r}")
            has_templates = True
        else:
            relative = Path("source", *parts)
        if relative in destinations:
            raise ValueError(f"Duplicate output path: {relative}")
        destinations.add(relative)
        plan.append((member, relative))
    if not has_templates:
        raise ValueError(f"Selected commit has no {TEMPLATES}/ templates")
    # A file must not also be an ancestor of another output file.
    for relative in destinations:
        if any(parent in destinations for parent in relative.parents):
            raise ValueError(f"Conflicting output file and directory: {relative}")
    return plan


def export_source(repository: Path, ref: str, output: Path) -> dict:
    repository = repository.resolve(strict=True)
    output = output.expanduser().absolute()
    # is_symlink also catches a dangling link, for which exists() is false.
    if output.exists() or output.is_symlink():
        raise FileExistsError(f"Output already exists; preserve it and choose a new path: {output}")
    parent = output.parent.resolve(strict=True)
    output = parent / output.name
    commit = git(repository, "rev-parse", "--verify", "--end-of-options", ref + "^{commit}")
    commit = commit.decode("ascii").strip()
    tree = git(repository, "rev-parse", "--verify", commit + "^{tree}").decode("ascii").strip()
    entries = git(repository, "ls-tree", "-r", "-z", "--full-tree", commit)
    if any(entry.startswith(b"160000 ") for entry in entries.split(b"\0")):
        raise ValueError("Submodules require separate pinned exports; none were exported")
    manifest = {
        "schema_version": 1,
        "kind": "intranet-source-and-templates",
        "complete_offline_bundle": False,
        "git": {"requested_ref": ref, "commit": commit, "tree": tree},
        "product_directory": "source",
        "deployment_templates": TEMPLATES,
        "requires_local_assets": [
            "images/offline-images.tar.gz",
            "runtime binaries and third-party dependency files",
            "optional vllm binaries, compatibility overlay and model metadata",
            "release-specific verification evidence and transport manifests",
        ],
        "files": [],
    }
    with tempfile.TemporaryFile() as stream:
        archive_commit(repository, commit, stream)
        with tarfile.open(fileobj=stream, mode="r:") as archive:
            plan = export_plan(archive)
            # Atomic reservation: another export, file or link must never be overwritten.
            output.mkdir(mode=0o755)
            try:
                for member, relative in sorted(plan, key=lambda item: str(item[1])):
                    destination = output / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    with archive.extractfile(member) as source, destination.open("xb") as target:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            target.write(chunk)
                            digest.update(chunk)
                    destination.chmod(0o755 if member.mode & 0o111 else 0o644)
                    manifest["files"].append({
                        "path": relative.as_posix(),
                        "git_path": member.name,
                        "bytes": member.size,
                        "sha256": digest.hexdigest(),
                    })
                with (output / MANIFEST).open("x", encoding="utf-8") as target:
                    json.dump(manifest, target, ensure_ascii=False, indent=2)
                    target.write("\n")
            except BaseException:
                shutil.rmtree(output)
                raise
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", required=True, help="Git commit or release tag to export")
    parser.add_argument("--output", required=True, type=Path, help="New directory; parent must exist")
    args = parser.parse_args()
    try:
        manifest = export_source(Path(__file__).resolve().parents[1], args.ref, args.output)
    except (OSError, ValueError, tarfile.TarError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"Export failed: {error}\n")
    print(json.dumps({
        "output": str(args.output.expanduser().absolute()),
        "git": manifest["git"],
        "files": len(manifest["files"]),
        "complete_offline_bundle": False,
        "notice": "Source/template export only; offline binaries and release evidence are not bundled.",
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
