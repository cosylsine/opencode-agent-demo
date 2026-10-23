import hashlib
import io
import json
import subprocess
import tarfile

import pytest

from scripts import export_intranet_source as exporter


def git(repository, *arguments):
    return subprocess.check_output(["git", "-C", str(repository), *arguments], text=True).strip()


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Export Test")
    git(root, "config", "user.email", "export-test@example.invalid")
    for name, text in {
        "app.py": "print('released')\n",
        ".opencode/agents/control.md": "Released control instructions\n",
        "deploy/tailnet.env.example": "HOST=127.0.0.1\n",
        "deploy/intranet/deploy.sh": "#!/bin/sh\nprintf 'released'\n",
        "deploy/intranet/config/demo.env.example": "VLLM_API_KEY=EMPTY\n",
    }.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (root / "deploy/intranet/deploy.sh").chmod(0o755)
    git(root, "add", ".")
    git(root, "commit", "-qm", "Release baseline")
    git(root, "tag", "-a", "INTRANET_20261021", "-m", "Release baseline")
    return root


def test_fixed_ref_excludes_later_commits_and_dirty_worktree(repository, tmp_path):
    commit = git(repository, "rev-parse", "INTRANET_20261021^{commit}")
    tree = git(repository, "rev-parse", commit + "^{tree}")
    (repository / "app.py").write_text("print('later commit')\n")
    git(repository, "add", "app.py")
    git(repository, "commit", "-qm", "Later change")
    (repository / "app.py").write_text("print('dirty secret')\n")
    (repository / "deploy/intranet/deploy.sh").write_text("dirty deployment\n")
    (repository / "credentials.env").write_text("UNTRACKED_SECRET=private\n")
    output = tmp_path / "export"

    manifest = exporter.export_source(repository, "INTRANET_20261021", output)

    assert (output / "source/app.py").read_text() == "print('released')\n"
    assert (output / "deploy.sh").read_text() == "#!/bin/sh\nprintf 'released'\n"
    assert (output / "deploy.sh").stat().st_mode & 0o111
    assert (output / "source/.opencode/agents/control.md").is_file()
    assert (output / "source/deploy/tailnet.env.example").is_file()
    assert (output / "config/demo.env.example").is_file()
    assert not (output / "source/deploy/intranet").exists()
    assert not (output / "source/credentials.env").exists()
    assert not any(p.name == ".git" for p in output.rglob("*"))
    assert manifest["git"] == {
        "requested_ref": "INTRANET_20261021", "commit": commit, "tree": tree,
    }
    assert manifest["complete_offline_bundle"] is False
    assert json.loads((output / exporter.MANIFEST).read_text()) == manifest
    for record in manifest["files"]:
        contents = (output / record["path"]).read_bytes()
        assert record["sha256"] == hashlib.sha256(contents).hexdigest()
        assert record["bytes"] == len(contents)
    second = tmp_path / "second-export"
    exporter.export_source(repository, commit, second)
    assert (second / "source/app.py").read_bytes() == (output / "source/app.py").read_bytes()


@pytest.mark.parametrize("kind", ["directory", "file", "dangling-symlink"])
def test_existing_output_is_preserved(repository, tmp_path, kind):
    output = tmp_path / "existing"
    if kind == "directory":
        output.mkdir()
        (output / "keep.txt").write_text("existing release")
    elif kind == "file":
        output.write_text("existing release")
    else:
        output.symlink_to(tmp_path / "missing-target")
    with pytest.raises(FileExistsError, match="already exists"):
        exporter.export_source(repository, "INTRANET_20261021", output)
    if kind == "directory":
        assert list(output.iterdir()) == [output / "keep.txt"]
        assert (output / "keep.txt").read_text() == "existing release"
    elif kind == "file":
        assert output.read_text() == "existing release"
    else:
        assert output.is_symlink()
        assert not (tmp_path / "missing-target").exists()


@pytest.mark.parametrize("entry", [
    "../escape.txt", "/absolute.txt", "deploy/intranet/../../escape.txt",
    "deploy/intranet/source/overlap.txt", "deploy/intranet/SOURCE-EXPORT.json",
    "nested/.git/config", "windows\\..\\escape.txt",
])
def test_unsafe_archive_paths_fail_before_output(repository, tmp_path, monkeypatch, entry):
    def unsafe_archive(_repository, _commit, stream):
        with tarfile.open(fileobj=stream, mode="w") as archive:
            for name in ["deploy/intranet/deploy.sh", entry]:
                info = tarfile.TarInfo(name)
                info.size = 4
                archive.addfile(info, io.BytesIO(b"data"))
        stream.seek(0)

    monkeypatch.setattr(exporter, "archive_commit", unsafe_archive)
    output = tmp_path / "export"
    with pytest.raises(ValueError):
        exporter.export_source(repository, "INTRANET_20261021", output)
    assert not output.exists()
    assert not (tmp_path / "escape.txt").exists()


def test_committed_symlink_is_rejected(repository, tmp_path):
    (repository / "external-link").symlink_to("../../outside")
    git(repository, "add", "external-link")
    git(repository, "commit", "-qm", "Unsafe symlink")
    output = tmp_path / "export"
    with pytest.raises(ValueError, match="links are forbidden"):
        exporter.export_source(repository, "HEAD", output)
    assert not output.exists()


def test_missing_templates_are_not_reported_as_a_release(repository, tmp_path):
    git(repository, "rm", "-r", "deploy/intranet")
    git(repository, "commit", "-qm", "Remove intranet templates")
    with pytest.raises(ValueError, match="has no deploy/intranet"):
        exporter.export_source(repository, "HEAD", tmp_path / "export")
    assert not (tmp_path / "export").exists()
