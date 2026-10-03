"""`write_config_sub_change.rejection_detail` (PR-SB-1.7): what the adaptor said when it refused a change.

Additive and nullable: the previous release neither reads nor writes the column, and every existing row simply has no detail.
`ran-nf-oam/app/models.py` is the model.

Revision ID: 0006
Revises: 0005
"""
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE write_config_sub_change ADD COLUMN rejection_detail TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE write_config_sub_change DROP COLUMN rejection_detail")
