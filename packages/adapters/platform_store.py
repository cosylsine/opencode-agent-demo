"""Persistent users and versioned office resources; independent of process-local sessions."""

import hashlib
import hmac
import re
import secrets
import time

from sqlalchemy import JSON, Boolean, Column, Integer, MetaData, String, Table, select
from sqlalchemy.exc import IntegrityError

from packages.contracts.common import now, uid
from packages.domain.core import DomainError

metadata = MetaData()
users = Table(
    "platform_user",
    metadata,
    Column("id", String, primary_key=True),
    Column("username", String, unique=True, nullable=False),
    Column("display_name", String, nullable=False),
    Column("password_hash", String, nullable=False),
    Column("is_admin", Boolean, nullable=False),
    Column("disabled", Boolean, nullable=False),
    Column("must_change_password", Boolean, nullable=False),
    Column("created_at", String, nullable=False),
)
sessions = Table(
    "platform_session",
    metadata,
    Column("hash", String, primary_key=True),
    Column("user_id", String, nullable=False, index=True),
    Column("csrf", String, nullable=False),
    Column("expires", Integer, nullable=False),
)
resources = Table(
    "office_resource",
    metadata,
    Column("id", String, primary_key=True),
    Column("kind", String, nullable=False, index=True),
    Column("owner_id", String, nullable=False, index=True),
    Column("visibility", String, nullable=False),
    Column("builtin", Boolean, nullable=False),
    Column("archived", Boolean, nullable=False),
    Column("published_version", Integer, nullable=False),
    Column("draft", JSON, nullable=False),
    Column("created_at", String, nullable=False),
)
versions = Table(
    "office_resource_version",
    metadata,
    Column("resource_id", String, primary_key=True),
    Column("version", Integer, primary_key=True),
    Column("content", JSON, nullable=False),
    Column("created_at", String, nullable=False),
)
files = Table(
    "office_file",
    metadata,
    Column("id", String, primary_key=True),
    Column("owner_id", String, nullable=False, index=True),
    Column("conversation_id", String),
    Column("execution_id", String),
    Column("data", JSON, nullable=False),
)
conversations = Table(
    "office_conversation",
    metadata,
    Column("id", String, primary_key=True),
    Column("owner_id", String, nullable=False, index=True),
    Column("data", JSON, nullable=False),
)
messages = Table(
    "office_message",
    metadata,
    Column("id", String, primary_key=True),
    Column("conversation_id", String, nullable=False, index=True),
    Column("data", JSON, nullable=False),
)
executions = Table(
    "office_execution",
    metadata,
    Column("id", String, primary_key=True),
    Column("owner_id", String, nullable=False, index=True),
    Column("status", String, nullable=False, index=True),
    Column("data", JSON, nullable=False),
)
owners = Table(
    "resource_owner",
    metadata,
    Column("id", String, primary_key=True),
    Column("owner_id", String, nullable=False, index=True),
)
tool_settings = Table(
    "office_tool_setting",
    metadata,
    Column("id", String, primary_key=True),
    Column("enabled", Boolean, nullable=False),
)


def password_hash(password):
    if not 10 <= len(password) <= 128:
        raise DomainError("INVALID_PASSWORD", "密码长度须为 10–128 个字符", 422)
    salt = secrets.token_bytes(16)
    value = hashlib.scrypt(password.encode(), salt=salt, n=16384, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${value.hex()}"


def password_matches(password, encoded):
    try:
        kind, salt, expected = encoded.split("$")
        if kind != "scrypt" or len(password) > 128:
            return False
        actual = hashlib.scrypt(
            password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1, dklen=32
        )
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


def token_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


def user_public(row):
    return {
        k: row[k]
        for k in ("id", "username", "display_name", "is_admin", "disabled", "must_change_password")
    }


class Platform:
    def __init__(self, db, catalog_root):
        self.db, self.engine, self.root, self.catalog_root = db, db.engine, db.root, catalog_root

    def initialized(self):
        with self.engine.connect() as c:
            return c.execute(select(users.c.id).where(users.c.is_admin)).first() is not None

    def create_user(self, username, display_name, password, admin=False):
        username = username.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,39}", username) or not display_name.strip():
            raise DomainError(
                "INVALID_USERNAME", "用户名须为 3–40 位字母、数字、点、横线或下划线", 422
            )
        row = dict(
            id=uid(),
            username=username,
            display_name=display_name.strip()[:80],
            password_hash=password_hash(password),
            is_admin=admin,
            disabled=False,
            must_change_password=False,
            created_at=now(),
        )
        try:
            with self.engine.begin() as c:
                c.execute(users.insert().values(**row))
        except IntegrityError as exc:
            raise DomainError("USERNAME_EXISTS", "该用户名已注册", 409) from exc
        return user_public(row)

    def get_user(self, user_id):
        with self.engine.connect() as c:
            row = c.execute(select(users).where(users.c.id == user_id)).mappings().first()
        if not row or row["disabled"]:
            raise DomainError("UNAUTHENTICATED", "请重新登录", 401)
        return dict(row)

    def login(self, username, password):
        with self.engine.connect() as c:
            row = (
                c.execute(select(users).where(users.c.username == username.strip().lower()))
                .mappings()
                .first()
            )
        # Run scrypt even for unknown users to avoid a fast username oracle.
        encoded = row["password_hash"] if row else "scrypt$" + "00" * 16 + "$" + "00" * 32
        if not password_matches(password, encoded) or not row or row["disabled"]:
            raise DomainError("LOGIN_FAILED", "用户名或密码错误，或账号已停用", 401)
        token, csrf = secrets.token_urlsafe(40), secrets.token_urlsafe(32)
        with self.engine.begin() as c:
            c.execute(sessions.delete().where(sessions.c.expires < int(time.time())))
            c.execute(
                sessions.insert().values(
                    hash=token_hash(token),
                    user_id=row["id"],
                    csrf=csrf,
                    expires=int(time.time()) + 43200,
                )
            )
        return token, csrf, user_public(row)

    def session(self, token):
        if not token:
            return None
        with self.engine.connect() as c:
            row = (
                c.execute(
                    select(sessions).where(
                        sessions.c.hash == token_hash(token), sessions.c.expires > int(time.time())
                    )
                )
                .mappings()
                .first()
            )
        if row:
            try:
                return self.get_user(row["user_id"]), row["csrf"]
            except DomainError:
                return None
        return None

    def logout(self, token):
        with self.engine.begin() as c:
            c.execute(sessions.delete().where(sessions.c.hash == token_hash(token)))

    def change_password(self, user_id, current, replacement):
        row = self.get_user(user_id)
        if not password_matches(current, row["password_hash"]):
            raise DomainError("PASSWORD_INCORRECT", "当前密码不正确", 403)
        self.update_user(user_id, password=replacement, force=False)

    def update_user(self, user_id, disabled=None, password=None, force=True):
        with self.engine.begin() as c:
            row = c.execute(select(users).where(users.c.id == user_id)).mappings().first()
            if not row:
                raise DomainError("NOT_FOUND", status=404)
            if row["is_admin"] and disabled:
                raise DomainError("ADMIN_PROTECTED", "不能停用初始管理员", 409)
            values = {}
            if disabled is not None:
                values["disabled"] = disabled
            if password is not None:
                values.update(password_hash=password_hash(password), must_change_password=force)
            if values:
                c.execute(users.update().where(users.c.id == user_id).values(**values))
                c.execute(sessions.delete().where(sessions.c.user_id == user_id))
            return user_public({**dict(row), **values})

    def claim(self, resource_id, user_id, conn):
        owner = conn.execute(
            select(owners.c.owner_id).where(owners.c.id == resource_id)
        ).scalar_one_or_none()
        if owner is None:
            conn.execute(owners.insert().values(id=resource_id, owner_id=user_id))
        elif owner != user_id:
            raise DomainError("NOT_FOUND", status=404)

    def owns(self, resource_id, user_id, conn=None):
        if conn is None:
            with self.engine.connect() as c:
                return self.owns(resource_id, user_id, c)
        return (
            conn.execute(
                select(owners.c.owner_id).where(owners.c.id == resource_id)
            ).scalar_one_or_none()
            == user_id
        )

    def require_owner(self, resource_id, user_id, conn=None):
        if not self.owns(resource_id, user_id, conn):
            raise DomainError("NOT_FOUND", status=404)

    def initialize_admin(self, username, display_name, password):
        if self.initialized():
            raise DomainError(
                "ALREADY_INITIALIZED", "初始管理员已创建；请使用管理员页面管理账号", 409
            )
        user = self.create_user(username, display_name, password, admin=True)
        self.import_legacy(user["id"])
        return user

    def import_legacy(self, admin_id):
        from packages.adapters.catalog import read_catalog
        from packages.adapters.storage import chat_sessions, runs, tuning_runs

        with self.engine.begin() as c:
            for table in (runs, tuning_runs):
                for resource_id in c.execute(select(table.c.id)).scalars():
                    if (
                        c.execute(select(owners.c.id).where(owners.c.id == resource_id)).first()
                        is None
                    ):
                        self.claim(resource_id, admin_id, c)
            c.execute(
                chat_sessions.update()
                .where(chat_sessions.c.user_id.not_in(select(users.c.id)))
                .values(user_id=admin_id)
            )
        # Deterministic IDs make repeated imports safe and never overwrite edits.
        from packages.adapters.office_resources import ResourceService

        service = ResourceService(self)
        catalog = read_catalog(self.catalog_root)
        for item in [*catalog.skills, *catalog.deleted_skills]:
            if not item.deletable:
                continue
            path = (
                self.catalog_root
                / ".opencode"
                / ("deleted-skills" if item.deleted else "skills")
                / item.name
                / "SKILL.md"
            )
            resource_id = "legacy-" + item.name
            if not service.exists(resource_id) and not service.deleted(resource_id):
                service.create(
                    "skill",
                    admin_id,
                    dict(
                        title=item.title[:80],
                        description=item.description[:1000] or item.title,
                        instructions=path.read_text(encoding="utf-8")[:12000],
                        tools=[],
                    ),
                    resource_id=resource_id,
                )
                if item.deleted:
                    service.state(resource_id, admin_id, archived=True)
        service.seed(admin_id)

    def admin_id(self):
        with self.engine.connect() as c:
            return (
                c.execute(select(users.c.id).where(users.c.is_admin).order_by(users.c.created_at))
                .scalars()
                .first()
            )
