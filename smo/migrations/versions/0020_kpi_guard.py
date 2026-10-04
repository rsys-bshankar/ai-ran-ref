"""`write_config_job.kpi_guard*` (PR-MSG-4): a KPI guard declared with a CM job, checked by the worker.

Three nullable columns on an existing table, so the previous release (which neither reads nor writes them) is unaffected.
`ran-nf-oam/app/models.py` is the model.

Revision ID: 0020
Revises: 0019
"""
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE write_config_job ADD COLUMN kpi_guard JSON")
    op.execute("ALTER TABLE write_config_job ADD COLUMN kpi_guard_result JSON")
    op.execute("ALTER TABLE write_config_job ADD COLUMN kpi_guard_checked_at TIMESTAMP WITH TIME ZONE")


def downgrade() -> None:
    op.execute("ALTER TABLE write_config_job DROP COLUMN kpi_guard_checked_at")
    op.execute("ALTER TABLE write_config_job DROP COLUMN kpi_guard_result")
    op.execute("ALTER TABLE write_config_job DROP COLUMN kpi_guard")
