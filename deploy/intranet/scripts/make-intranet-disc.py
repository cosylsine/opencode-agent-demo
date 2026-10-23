"""Build a complete cumulative update DVD; exclude host state and old releases."""
import hashlib
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "delivery/intranet-20261021"
EXCLUDE = {"node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", ".venv", "var",
           ".impeccable", "test-results", "playwright-report", "dist-copy-update", ".git"}
DIRECTORIES = ["source", "build", "runtime", "scripts", "images"]
FILES = ["Dockerfile", ".dockerignore", "deploy.sh", "README.md", "fix403.md",
         "fix-tuning.md", "office-v1.md", "INTRANET-UPDATE-20261021.md"]
# Only release checks are carried, not private historical model tasks or local data.
EVIDENCE = ["RESULTS.md", "pytest-final.txt", "pytest-image.txt", "ruff.txt", "frontend-build.txt",
            "browser-modern-verified.txt", "browser-chromium86-verified.txt", "lan-tcp-final.txt",
            "image-build-final.txt", "image-source-check.json", "archive-load.txt",
            "rehearsal-check.txt", "rehearsal-office.txt", "migration-rollback.json",
            "cleanup.json"]


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(part)
    return h.hexdigest()


def selected_files():
    result = set(FILES)
    for name in DIRECTORIES:
        for path in (ROOT / name).rglob("*"):
            rel = path.relative_to(ROOT)
            if any(x in EXCLUDE for x in rel.parts) or path.suffix in {".pyc", ".pyo"}:
                continue
            if path.is_symlink():
                raise ValueError(f"Unexpected release symlink: {rel}")
            if path.is_file():
                result.add(str(rel))
    for name in EVIDENCE:
        result.add("verification/release-20261021/" + name)
    for name in ["lan_tcp_checks.py", "rehearsal_entry.py", "rehearsal_model.py",
                 "test_lan_runtime.py", "test_mcp_config.py", "chrome86_browser.cjs"]:
        result.add("verification/" + name)
    return sorted(result)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--rehearsal", action="store_true", help="Stage under .rehearsal; allow unfinished evidence")
    args = parser.parse_args()
    output = ROOT / "delivery/.intranet-rehearsal" if args.rehearsal else OUT
    if output.exists():
        raise SystemExit(f"Output already exists, preserve it before rebuilding: {output}")
    names = selected_files()
    if args.rehearsal:
        names = [name for name in names if (ROOT / name).exists()]
    for name in names:
        if not (ROOT / name).is_file():
            raise SystemExit("Missing required evidence/file: " + name)
    manifest = "".join(digest(ROOT / n) + "  " + n + "\n" for n in names)
    if not args.rehearsal:
        (ROOT / "RELEASE-SHA256SUMS").write_text(manifest)
    with tempfile.TemporaryDirectory(prefix=".intranet-stage-", dir=ROOT / "delivery") as temp:
        disc = Path(temp) / "disc-01"
        payload = disc / "payload"
        for name in names:
            destination = payload / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / name, destination)
        (payload / "RELEASE-SHA256SUMS").write_text(manifest)
        (disc / "scripts").mkdir()
        shutil.copy2(ROOT / "scripts/intranet-update.sh", disc / "scripts/intranet-update.sh")
        shutil.copy2(ROOT / "INTRANET-UPDATE-20261021.md", disc / "INTRANET-UPDATE-20261021.md")
        (disc / "START-HERE.txt").write_text(
            "20261021 累计更新盘：只刻本目录内全部内容到一张数据 DVD 根目录。\n"
            "不必安装任何旧的中间更新盘。先读 INTRANET-UPDATE-20261021.md。\n"
            "将全盘复制到硬盘独立收件目录；sha256sum -c DISC-SHA256SUMS --quiet。\n"
            "随后按手册 check → apply → init-admin → up → check → 真实浏览器/模型验收。\n"
            "保留原 config/demo.env 和 data；不要手工把 payload 直接覆盖进去。\n"
            + ("此为制作机演练副本，不可刻盘。\n" if args.rehearsal else "正式更新交付版。\n"))
        (disc / "MANAGED-PATHS.txt").write_text("".join(p.name + "\n" for p in sorted(payload.iterdir())))
        metadata = json.loads((ROOT / "images/IMAGE-MANIFEST.json").read_text())
        metadata["disc_count"] = 1
        metadata["release"] = "20261021"
        metadata["baseline"] = "existing installation after LAN 403 and Chrome 86 fixes"
        metadata["payload_files"] = len(names) + 1
        metadata["payload_bytes"] = sum(p.stat().st_size for p in payload.rglob("*") if p.is_file())
        (disc / "RELEASE-INFO.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
        checks = sorted(p for p in disc.rglob("*") if p.is_file())
        (disc / "DISC-SHA256SUMS").write_text("".join(digest(p) + "  " + str(p.relative_to(disc)) + "\n" for p in checks))
        total = sum(p.stat().st_size for p in disc.rglob("*") if p.is_file())
        if total >= 4_000_000_000:
            raise SystemExit("DVD capacity exceeded")
        subprocess.run(["sha256sum", "-c", "DISC-SHA256SUMS", "--quiet"], cwd=disc, check=True)
        subprocess.run(["bash", "deploy.sh", "verify"], cwd=payload, check=True)
        output.mkdir()
        disc.rename(output / "disc-01")
    print(json.dumps({"directory": str(output / "disc-01"), "bytes": total,
                      "MiB": round(total / 1024**2, 2), "files": len(checks) + 1}, indent=2))


if __name__ == "__main__":
    main()
