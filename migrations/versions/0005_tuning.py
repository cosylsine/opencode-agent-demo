"""Independent steam-cycle B tuning records."""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE TABLE IF NOT EXISTS tuning_run (id VARCHAR PRIMARY KEY, active BOOLEAN NOT NULL, version INTEGER NOT NULL, data JSON NOT NULL)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_active_tuning ON tuning_run(active) WHERE active = 1")


def downgrade():
    op.execute("DROP INDEX IF EXISTS uq_active_tuning")
    op.execute("DROP TABLE IF EXISTS tuning_run")
