"""`o1_adaptor_host_key` (PR-SB-2.3): the SSH host keys an operator pinned for an ssh endpoint.

A new table, so the previous release (which does not know it) is unaffected: it connects with `NETCONF_SSH_KNOWN_HOSTS` as before. The public key
only (never anything private), at most one per key type per endpoint; rows go with their endpoint. `ran-nf-oam/app/models.py` is the model.

Revision ID: 0008
Revises: 0007
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create `o1_adaptor_host_key`: the pinned public SSH host keys, at most one per key type per endpoint, deleted with their endpoint."""
    op.execute("""
        CREATE TABLE o1_adaptor_host_key (
            id UUID PRIMARY KEY,
            endpoint_id UUID NOT NULL REFERENCES o1_adaptor_endpoint (endpoint_id) ON DELETE CASCADE,
            key_type TEXT NOT NULL,
            public_key TEXT NOT NULL,
            fingerprint TEXT NOT NULL,
            pinned_by TEXT NOT NULL,
            pinned_at TIMESTAMPTZ NOT NULL,
            UNIQUE (endpoint_id, key_type)
        )
    """)


def downgrade() -> None:
    """Drop `o1_adaptor_host_key`."""
    op.execute("DROP TABLE o1_adaptor_host_key")
