"""`write_config_job.rollback_of` and `rollback_forced` (PR-MGT-1.6, 1.7): a job that undoes another one says which, and whether the
requester overrode the changed-since guard.

Additive: a nullable column and a boolean with a default, so the previous release (which neither reads nor writes them) is unaffected.
`ran-nf-oam/app/models.py` is the model.

Revision ID: 0011
Revises: 0010
"""
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add `write_config_job.rollback_of` (nullable) and `rollback_forced` (NOT NULL, default false)."""
    op.execute("ALTER TABLE write_config_job ADD COLUMN rollback_of UUID")
    op.execute("ALTER TABLE write_config_job ADD COLUMN rollback_forced BOOLEAN NOT NULL DEFAULT false")


def downgrade() -> None:
    """Drop the two columns."""
    op.execute("ALTER TABLE write_config_job DROP COLUMN rollback_forced")
    op.execute("ALTER TABLE write_config_job DROP COLUMN rollback_of")
