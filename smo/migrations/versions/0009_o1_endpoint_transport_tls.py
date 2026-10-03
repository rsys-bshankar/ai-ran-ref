"""`o1_adaptor_endpoint.transport` may be 'tls' (PR-SB-2.4): NETCONF over TLS (RFC 7589) with a client certificate.

Widens the CHECK of revision 0003 to ('http-mock', 'ssh', 'tls'), under a new name (`o1_adaptor_endpoint_transport_known`) so that the schema
differs from the previous revision's (the round-trip test compares constraint names). Existing rows are untouched. The previous release never writes 'tls', so it keeps
working on this schema. The downgrade restores the narrower check and therefore fails, deliberately, while any row still says 'tls': re-register
those endpoints first rather than have a downgrade rewrite what an operator configured.

Revision ID: 0009
Revises: 0008
"""
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE o1_adaptor_endpoint DROP CONSTRAINT o1_adaptor_endpoint_transport_check")
    op.execute("ALTER TABLE o1_adaptor_endpoint ADD CONSTRAINT o1_adaptor_endpoint_transport_known CHECK (transport IN ('http-mock', 'ssh', 'tls'))")


def downgrade() -> None:
    op.execute("ALTER TABLE o1_adaptor_endpoint DROP CONSTRAINT o1_adaptor_endpoint_transport_known")
    op.execute("ALTER TABLE o1_adaptor_endpoint ADD CONSTRAINT o1_adaptor_endpoint_transport_check CHECK (transport IN ('http-mock', 'ssh'))")
