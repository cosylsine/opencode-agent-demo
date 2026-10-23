"""Persist user-facing conversations independently from business runs."""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE TABLE IF NOT EXISTS chat_session (id VARCHAR PRIMARY KEY, user_id VARCHAR NOT NULL, title VARCHAR NOT NULL, created_at VARCHAR NOT NULL)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chat_session_user ON chat_session(user_id, created_at)"
    )
    op.execute(
        "CREATE TABLE IF NOT EXISTS chat_message (id VARCHAR PRIMARY KEY, session_id VARCHAR NOT NULL, role VARCHAR NOT NULL, content VARCHAR NOT NULL, created_at VARCHAR NOT NULL, run_id VARCHAR, request_key VARCHAR, request_hash VARCHAR, status VARCHAR NOT NULL, FOREIGN KEY(session_id) REFERENCES chat_session(id))"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chat_message_session ON chat_message(session_id, created_at)"
    )
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_chat_message_request ON chat_message(session_id, request_key, role) WHERE request_key IS NOT NULL"
    )


def downgrade():
    op.execute("DROP TABLE chat_message")
    op.execute("DROP TABLE chat_session")
