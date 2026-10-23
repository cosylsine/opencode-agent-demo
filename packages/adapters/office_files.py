"""Private file IDs and cancellable subprocess tools."""

import asyncio
import hashlib
import json
import mimetypes
import os
import shutil
import sys
import tempfile
from pathlib import Path

from sqlalchemy import select

from packages.adapters.office_tools import validate_arguments
from packages.adapters.platform_store import files, tool_settings
from packages.contracts.common import now, uid
from packages.contracts.office import FileInfo
from packages.domain.core import DomainError

ALLOWED = {".docx", ".xlsx", ".csv", ".txt", ".md", ".pdf", ".zip"}


class FileService:
    def __init__(self, platform):
        self.p = platform
        self.root = platform.root / "office" / "files"
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.max_size = int(os.getenv("OFFICE_MAX_FILE_BYTES", str(20 * 1024 * 1024)))

    def info(self, row):
        d = row["data"]
        return FileInfo(
            id=row["id"],
            name=d["name"],
            mime=d["mime"],
            size=d["size"],
            created_at=d["created_at"],
            download_url="/api/v1/files/" + row["id"] + "/download",
        )

    def require(self, file_id, user, allowed=None):
        if allowed is not None and file_id not in allowed:
            raise DomainError("FILE_FORBIDDEN", "文件不属于当前任务", 403)
        with self.p.engine.connect() as c:
            row = (
                c.execute(select(files).where(files.c.id == file_id, files.c.owner_id == user))
                .mappings()
                .first()
            )
        if not row:
            raise DomainError("NOT_FOUND", status=404)
        path = self.root / user / (file_id + row["data"]["suffix"])
        if (
            path.is_symlink()
            or not path.is_file()
            or not path.resolve().is_relative_to(self.root.resolve())
        ):
            raise DomainError("FILE_UNAVAILABLE", "文件暂不可用", 404)
        return dict(row), path

    def register(self, source, name, user, execution_id=None, conversation_id=None, file_id=None):
        name = Path(name.replace("\\", "/")).name
        name = "".join(c for c in name if ord(c) >= 32 and ord(c) != 127)[:160]
        suffix = Path(name).suffix.lower()
        if suffix not in ALLOWED:
            raise DomainError(
                "UNSUPPORTED_FILE", "支持 DOCX、XLSX、CSV、TXT、Markdown、PDF 和 ZIP", 422
            )
        size = source.stat().st_size
        if not 0 < size <= self.max_size:
            raise DomainError("FILE_TOO_LARGE", "文件为空或超过允许大小", 413)
        checksum = hashlib.sha256(source.read_bytes()).hexdigest()
        if file_id is not None:
            with self.p.engine.connect() as c:
                existing = (
                    c.execute(select(files).where(files.c.id == file_id, files.c.owner_id == user))
                    .mappings()
                    .first()
                )
            if existing:
                if existing["data"]["sha256"] != checksum or existing["data"]["name"] != name:
                    raise DomainError("IDEMPOTENCY_CONFLICT", "同一上传请求不能替换文件内容", 409)
                return self.info(existing)
        file_id = file_id or uid()
        directory = self.root / user
        directory.mkdir(mode=0o700, exist_ok=True)
        destination = directory / (file_id + suffix)
        shutil.copyfile(source, destination)
        os.chmod(destination, 0o600)
        data = dict(
            name=name,
            suffix=suffix,
            mime=mimetypes.guess_type(name)[0] or "application/octet-stream",
            size=size,
            sha256=checksum,
            created_at=now(),
        )
        row = dict(
            id=file_id,
            owner_id=user,
            execution_id=execution_id,
            conversation_id=conversation_id,
            data=data,
        )
        try:
            with self.p.engine.begin() as c:
                c.execute(files.insert().values(**row))
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        return self.info(row)

    async def upload(self, upload, user, key):
        temporary_root = self.p.root / "office" / "tmp"
        temporary_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(dir=temporary_root) as directory:
            path = Path(directory) / "upload"
            count = 0
            with path.open("wb") as f:
                while chunk := await upload.read(65536):
                    count += len(chunk)
                    if count > self.max_size:
                        raise DomainError("FILE_TOO_LARGE", "附件超过文件大小上限", 413)
                    f.write(chunk)
            from uuid import UUID

            file_id = str(UUID(hashlib.sha256((user + ":upload:" + key).encode()).hexdigest()[:32]))
            return self.register(path, upload.filename or "附件.txt", user, file_id=file_id)

    def list_execution(self, execution_id, user):
        with self.p.engine.connect() as c:
            return [
                self.info(r)
                for r in c.execute(
                    select(files).where(
                        files.c.execution_id == execution_id, files.c.owner_id == user
                    )
                ).mappings()
            ]

    def conversation_ids(self, conversation_id, user):
        with self.p.engine.connect() as c:
            return set(
                c.execute(
                    select(files.c.id).where(
                        files.c.conversation_id == conversation_id, files.c.owner_id == user
                    )
                ).scalars()
            )


class ToolRunner:
    def __init__(self, platform, file_service):
        self.p, self.files = platform, file_service

    async def run(self, tool, arguments, user, execution_id, conversation_id=None, allowed=None):
        self.p.get_user(user)
        with self.p.engine.connect() as c:
            enabled = c.execute(
                select(tool_settings.c.enabled).where(tool_settings.c.id == tool)
            ).scalar_one_or_none()
        if enabled is False:
            raise DomainError("TOOL_DISABLED", "此工具已停用", 409)
        arguments = validate_arguments(tool, arguments)
        paths = {}
        for key, value in arguments.items():
            if key.endswith("file_id") and value:
                _, path = self.files.require(value, user, allowed)
                paths[key] = str(path)
            elif key == "file_ids":
                resolved, names = [], []
                for file_id in value:
                    row, path = self.files.require(file_id, user, allowed)
                    resolved.append(str(path))
                    names.append(row["data"]["name"])
                paths[key], paths["file_names"] = resolved, names
        temporary_root = self.p.root / "office" / "tmp"
        temporary_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with tempfile.TemporaryDirectory(dir=temporary_root) as directory:
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "packages.adapters.office_worker",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=str(Path(__file__).resolve().parents[2]),
            )
            try:
                output, _ = await asyncio.wait_for(
                    process.communicate(
                        json.dumps(
                            dict(tool=tool, arguments=arguments, paths=paths, output=directory),
                            ensure_ascii=False,
                        ).encode()
                    ),
                    timeout=60,
                )
            except (asyncio.TimeoutError, asyncio.CancelledError):
                if process.returncode is None:
                    process.kill()
                    await process.wait()
                raise
            if process.returncode or len(output) > 2 * 1024 * 1024:
                raise DomainError("TOOL_FAILED", "工具处理超时或资源超限，请缩小文件后重试", 422)
            data = json.loads(output)
            if data.get("error"):
                raise DomainError("TOOL_FAILED", data["error"], 422)
            result_files = []
            for artifact in data["artifacts"]:
                path = Path(artifact["path"])
                if path.is_symlink() or not path.resolve().is_relative_to(
                    Path(directory).resolve()
                ):
                    raise DomainError("INVALID_ARTIFACT", status=500)
                result_files.append(
                    self.files.register(path, artifact["name"], user, execution_id, conversation_id)
                )
            result = {**data["result"], "files": [f.model_dump() for f in result_files]}
            if "rows" in result:
                while (
                    result["rows"] and len(json.dumps(result, ensure_ascii=False).encode()) > 32000
                ):
                    result["rows"] = result["rows"][: len(result["rows"]) // 2]
                    result["preview_truncated"] = True
                if result.get("preview_truncated"):
                    result["warnings"] = [
                        *result.get("warnings", []),
                        "返回内容过大，已缩减预览；可选择较少字段继续读取，原始文件和计算结果未改变。",
                    ]
            return result
