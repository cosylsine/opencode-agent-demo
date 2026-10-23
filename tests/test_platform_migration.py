"""Upgrade a genuine v0005 database and restore private office data."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select

from packages.adapters.office_files import FileService
from packages.adapters.platform_store import Platform
from packages.adapters.storage import Store, chat_sessions
from packages.contracts.common import now, uid
from scripts.backup import backup, restore


def test_upgrade_v5_and_backup_restore_accounts_and_files(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    engine = create_engine("sqlite:///" + str(root / "demo.db"))
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).resolve().parents[1] / "migrations")
    )
    config.attributes["engine"] = engine
    command.upgrade(config, "0005")
    session_id = uid()
    with engine.begin() as conn:
        conn.execute(
            chat_sessions.insert().values(
                id=session_id,
                user_id="old-shared-proxy-user",
                title="升级前的会话",
                created_at=now(),
            )
        )
    engine.dispose()
    db = Store(root)
    platform = Platform(db, tmp_path / "catalog")
    admin = platform.initialize_admin("admin", "管理员", "migration-test-password")
    with db.engine.connect() as conn:
        assert (
            conn.execute(
                select(chat_sessions.c.user_id).where(chat_sessions.c.id == session_id)
            ).scalar_one()
            == admin["id"]
        )
    source = tmp_path / "private.txt"
    source.write_text("私有附件内容", encoding="utf-8")
    file = FileService(platform).register(source, "私有附件.txt", admin["id"])
    platform.import_legacy(admin["id"])
    db.close()
    backup(root, tmp_path / "backup")
    restored = tmp_path / "restored"
    restore(tmp_path / "backup", restored)
    db = Store(restored)
    try:
        platform = Platform(db, tmp_path / "catalog")
        token, _, account = platform.login("admin", "migration-test-password")
        assert account["id"] == admin["id"] and platform.session(token)
        _, path = FileService(platform).require(file.id, admin["id"])
        assert path.read_text(encoding="utf-8") == "私有附件内容"
    finally:
        db.close()
