"""`o1_adaptor_endpoint.transport` (PR-SB-1.2): how an O1 adaptor is reached.

Additive and defaulted: every existing row becomes 'http-mock', which is what it already was, and the previous release's
code (which neither reads nor writes the column) keeps working on this schema. `app/models.py` (ran-nf-oam) is the model.

Revision ID: 0003
Revises: 0002
"""
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE o1_adaptor_endpoint
          ADD COLUMN transport TEXT NOT NULL DEFAULT 'http-mock' CHECK (transport IN ('http-mock', 'ssh'))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE o1_adaptor_endpoint DROP COLUMN transport")
