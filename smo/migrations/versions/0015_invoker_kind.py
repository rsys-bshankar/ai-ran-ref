"""`invoker_registration.kind` (PR-SEC-14): an SMO module's invoker or an rApp's.

A column with a default, so the previous release (which inserts without it) is unaffected. Every invoker that exists when this runs is an SMO
module's or the GUI's (rApps got their identity from rApp Management, which did not register invokers at SME), so they keep `internal`: a rolling
upgrade does not lose its modules' scopes. `sme/app/models.py` is the model.

Revision ID: 0015
Revises: 0014
"""
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE invoker_registration ADD COLUMN kind VARCHAR NOT NULL DEFAULT 'internal'")


def downgrade() -> None:
    op.execute("ALTER TABLE invoker_registration DROP COLUMN kind")
