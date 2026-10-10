"""`o1_adaptor_endpoint.credential_ref` (PR-SB-2.1): the NAME of the credential an adaptor is reached with.

Additive and nullable: NULL means the shared credential of PR-SB-1 (`NETCONF_SSH_PASSWORD`), which is what every existing row used, and the
previous release neither reads nor writes the column. The value is a reference resolved from the service's own secrets at connect time
(`ran-nf-oam/app/netconf_ssh.py`, `credentials_for`); a secret itself is never stored here.

Revision ID: 0007
Revises: 0006
"""
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add the nullable `o1_adaptor_endpoint.credential_ref`."""
    op.execute("ALTER TABLE o1_adaptor_endpoint ADD COLUMN credential_ref TEXT")


def downgrade() -> None:
    """Drop the column `credential_ref`."""
    op.execute("ALTER TABLE o1_adaptor_endpoint DROP COLUMN credential_ref")
