"""Accounts, versioned resources and private office tasks; preserve legacy tables."""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    from packages.adapters.platform_store import metadata
    metadata.create_all(op.get_bind(), checkfirst=True)


def downgrade():
    from packages.adapters.platform_store import metadata
    metadata.drop_all(op.get_bind())
