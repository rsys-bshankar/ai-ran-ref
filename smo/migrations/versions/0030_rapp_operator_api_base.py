"""`rapp_instance.operator_api_base` (PR-GUI-8, step GUI-8.3): where a rApp instance's operator API is reached.

A nullable column on an existing table: the base URL (http or https) the instance registers, or an operator sets, for the routes its package declares in
`operatorUi` (docs/adr/0004-operator-ui-declaration.md, 4). R1 Termination resolves `/rapps/{instanceId}/operator/...` to it. `rapp-mgmt/app/models.py`
is the model.

Expand only: the previous release's code neither reads nor writes the column, and every row already there gets NULL, which means "no operator API registered".

Revision ID: 0030
Revises: 0029
"""
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the nullable `rapp_mgmt.rapp_instance.operator_api_base` (if it does not exist)."""
    op.execute("ALTER TABLE rapp_mgmt.rapp_instance ADD COLUMN IF NOT EXISTS operator_api_base TEXT")


def downgrade() -> None:
    """Drop the column if it exists."""
    op.execute("ALTER TABLE rapp_mgmt.rapp_instance DROP COLUMN IF EXISTS operator_api_base")
