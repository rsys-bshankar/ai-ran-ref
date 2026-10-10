"""`rapp_limit` and who/when on a config job (PR-AI-10.1/10.2): a per-rApp cap on CM write jobs.

A new table, two nullable columns on `write_config_job` and a defaulted one on `rapp_instance`, so the previous release (which does not know them) is unaffected. `ran-nf-oam/app/models.py` and `rapp-mgmt/app/models.py` are the models.

Revision ID: 0014
Revises: 0013
"""
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create `rapp_limit` (the per-rApp job cap), add `invoker_id` and `created_at` to `write_config_job` with an index on the invoker, and `rapp_limits_set` to `rapp_instance`."""
    op.execute("""
        CREATE TABLE rapp_limit (
            invoker_id               VARCHAR PRIMARY KEY,
            max_config_jobs_per_hour INTEGER NOT NULL,
            updated_at               TIMESTAMP WITH TIME ZONE NOT NULL
        )
    """)
    op.execute("ALTER TABLE write_config_job ADD COLUMN invoker_id VARCHAR")
    op.execute("ALTER TABLE write_config_job ADD COLUMN created_at TIMESTAMP WITH TIME ZONE")
    op.execute("CREATE INDEX ix_write_config_job_invoker_id ON write_config_job (invoker_id)")
    op.execute("ALTER TABLE rapp_instance ADD COLUMN rapp_limits_set BOOLEAN NOT NULL DEFAULT false")


def downgrade() -> None:
    """Drop the index, the columns and `rapp_limit`, in reverse order."""
    op.execute("ALTER TABLE rapp_instance DROP COLUMN rapp_limits_set")
    op.execute("DROP INDEX ix_write_config_job_invoker_id")
    op.execute("ALTER TABLE write_config_job DROP COLUMN created_at")
    op.execute("ALTER TABLE write_config_job DROP COLUMN invoker_id")
    op.execute("DROP TABLE rapp_limit")
