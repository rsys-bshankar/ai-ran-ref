"""`cm_snapshot` (PR-MGT-1.1): the before and after values of every dispatched CM sub-change.

Additive: a new table and one index; the previous release neither reads nor writes it. `ran-nf-oam/app/models.py` (CMSnapshot) is the model.

Revision ID: 0004
Revises: 0003
"""
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create `cm_snapshot` (one row per dispatched sub-change, deleted with its sub-change or its job) and the index that lists an element's snapshots newest first."""
    op.execute("""
        CREATE TABLE cm_snapshot (
          snapshot_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          sub_change_id        UUID NOT NULL UNIQUE REFERENCES write_config_sub_change(id) ON DELETE CASCADE,
          job_id               UUID NOT NULL REFERENCES write_config_job(job_id) ON DELETE CASCADE,
          managed_element_ref  TEXT NOT NULL,
          managed_function_ref TEXT,
          operation            TEXT NOT NULL,
          before               JSONB,
          after                JSONB,
          before_error         TEXT,
          created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    op.execute("CREATE INDEX cm_snapshot_element_time ON cm_snapshot (managed_element_ref, created_at DESC)")


def downgrade() -> None:
    """Drop `cm_snapshot`."""
    op.execute("DROP TABLE cm_snapshot")
