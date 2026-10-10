"""Notification outbox table (PR-MSG-1.2), the first revision after the baseline.

Additive: a new table and one index, nothing the previous release reads, so the previous release runs on this schema.
`smo_shared/outbox.py` is the model.

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create `notification_outbox` and its partial index on the pending rows by due time, which is what a drain reads."""
    op.execute("""
        CREATE TABLE notification_outbox (
          id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          module           TEXT NOT NULL,
          destination      TEXT NOT NULL,
          payload          JSONB NOT NULL,
          status           TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING', 'SENT', 'DEAD')),
          attempts         INTEGER NOT NULL DEFAULT 0,
          next_attempt_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
          last_error       TEXT,
          created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        )
    """)
    # what a drain asks: the pending rows that are due, oldest first
    op.execute("CREATE INDEX notification_outbox_due ON notification_outbox (next_attempt_at) WHERE status = 'PENDING'")


def downgrade() -> None:
    """Drop `notification_outbox`."""
    op.execute("DROP TABLE notification_outbox")
