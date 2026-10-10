"""`rapp_kill` (PR-AI-10.4): the per-rApp kill switch.

A new table, so the previous release (which does not know it) is unaffected. `ran-nf-oam/app/models.py` is the model.

Revision ID: 0016
Revises: 0015
"""
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create `rapp_kill`: one row per invoker whose writes are switched off, with who and why."""
    op.execute("""
        CREATE TABLE rapp_kill (
            invoker_id VARCHAR PRIMARY KEY,
            reason     VARCHAR,
            killed_by  VARCHAR NOT NULL,
            killed_at  TIMESTAMP WITH TIME ZONE NOT NULL
        )
    """)


def downgrade() -> None:
    """Drop `rapp_kill`."""
    op.execute("DROP TABLE rapp_kill")
