"""`notification_outbox.method` (PR-MSG-1.10): a row can be a DELETE as well as a POST.

Additive and defaulted: every existing row is a POST, which is what it was, and the previous release (which neither reads nor writes
the column) keeps working on this schema. `smo_shared/outbox.py` is the model.

Revision ID: 0005
Revises: 0004
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE notification_outbox
          ADD COLUMN method TEXT NOT NULL DEFAULT 'POST' CHECK (method IN ('POST', 'DELETE'))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE notification_outbox DROP COLUMN method")
