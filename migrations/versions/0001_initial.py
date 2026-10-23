"""Initial single-process store."""

from alembic import op

from packages.adapters.storage import metadata

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    metadata.create_all(op.get_bind())
    op.execute("CREATE UNIQUE INDEX uq_active_loop ON run(loop_id) WHERE active = 1")


def downgrade():
    metadata.drop_all(op.get_bind())
