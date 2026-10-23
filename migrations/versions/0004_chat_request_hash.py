"""Keep chat idempotency scoped to the complete request body."""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    columns = {row[1] for row in op.get_bind().exec_driver_sql("PRAGMA table_info(chat_message)")}
    if "request_hash" not in columns:
        op.execute("ALTER TABLE chat_message ADD COLUMN request_hash VARCHAR")


def downgrade():
    # SQLite column removal would rebuild the table; preserving this harmless
    # column keeps older chat records available during rollback.
    pass
