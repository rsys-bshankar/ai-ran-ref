"""`audit_log` and `audit_head` (PR-SEC-11): the tamper-evident record of changes made through the gateway.

Two new tables, so the previous release (which does not know them) is unaffected. `shared/smo_shared/audit.py` is the model. The head row is
seeded here: the writers lock it to number the rows.

Revision ID: 0021
Revises: 0020
"""
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE audit_log (
            seq            BIGINT PRIMARY KEY,
            audit_id       UUID NOT NULL UNIQUE,
            occurred_at    TIMESTAMP WITH TIME ZONE NOT NULL,
            actor          VARCHAR NOT NULL,
            actor_role     VARCHAR,
            action         VARCHAR NOT NULL,
            target         VARCHAR NOT NULL,
            result         VARCHAR NOT NULL,
            correlation_id VARCHAR,
            detail         JSON,
            prev_hash      VARCHAR(64) NOT NULL,
            hash           VARCHAR(64) NOT NULL
        )
    """)
    op.execute("CREATE INDEX ix_audit_log_occurred_at ON audit_log (occurred_at)")
    op.execute("CREATE INDEX ix_audit_log_actor ON audit_log (actor)")
    op.execute("CREATE INDEX ix_audit_log_correlation_id ON audit_log (correlation_id)")
    op.execute("""
        CREATE TABLE audit_head (
            head_id   INTEGER PRIMARY KEY,
            last_seq  BIGINT NOT NULL,
            last_hash VARCHAR(64) NOT NULL
        )
    """)
    op.execute(f"INSERT INTO audit_head (head_id, last_seq, last_hash) VALUES (1, 0, '{'0' * 64}')")


def downgrade() -> None:
    op.execute("DROP TABLE audit_head")
    op.execute("DROP TABLE audit_log")
