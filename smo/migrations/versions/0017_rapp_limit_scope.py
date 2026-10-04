"""`rapp_limit` takes blast-radius and magnitude limits (PR-AI-10.3).

Two nullable columns, and `max_config_jobs_per_hour` stops being required (a manifest may declare only a blast radius). The table is new in this
release, so the previous release neither reads nor writes it. `ran-nf-oam/app/models.py` is the model.

Revision ID: 0017
Revises: 0016
"""
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE rapp_limit ALTER COLUMN max_config_jobs_per_hour DROP NOT NULL")
    op.execute("ALTER TABLE rapp_limit ADD COLUMN max_elements_per_job INTEGER")
    op.execute("ALTER TABLE rapp_limit ADD COLUMN max_change_percent DOUBLE PRECISION")


def downgrade() -> None:
    op.execute("ALTER TABLE rapp_limit DROP COLUMN max_change_percent")
    op.execute("ALTER TABLE rapp_limit DROP COLUMN max_elements_per_job")
    op.execute("DELETE FROM rapp_limit WHERE max_config_jobs_per_hour IS NULL")
    op.execute("ALTER TABLE rapp_limit ALTER COLUMN max_config_jobs_per_hour SET NOT NULL")
