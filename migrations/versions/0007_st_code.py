"""Persist managed ST specialist tasks separately from editable office agents."""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    from packages.adapters.storage import st_runs
    st_runs.create(op.get_bind(), checkfirst=True)


def downgrade():
    op.drop_table("st_code_run")
