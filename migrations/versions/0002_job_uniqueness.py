"""Enforce one baseline per run and one candidate trial per iteration at DB level."""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "CREATE UNIQUE INDEX uq_baseline_trial ON trial_job(json_extract(data, '$.request.run_id')) WHERE json_extract(data, '$.request.purpose') = 'BASELINE'"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_candidate_trial ON trial_job(json_extract(data, '$.request.iteration_id')) WHERE json_extract(data, '$.request.purpose') = 'CANDIDATE'"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_iteration_number ON iteration(run_id, json_extract(data, '$.iteration_no'))"
    )


def downgrade():
    for name in ("uq_baseline_trial", "uq_candidate_trial", "uq_iteration_number"):
        op.execute("DROP INDEX " + name)
