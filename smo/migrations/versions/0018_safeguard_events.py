"""`safeguard_subscription` and `safeguard_refusal` (PR-AI-10.6): events for each refusal of an rApp.

Two new tables, so the previous release (which does not know them) is unaffected. `ran-nf-oam/app/models.py` is the model.

Revision ID: 0018
Revises: 0017
"""
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create `safeguard_subscription` (callbacks told of refusals) and `safeguard_refusal` (one row per refusal of an rApp) with indexes on the time and the invoker."""
    op.execute("""
        CREATE TABLE safeguard_subscription (
            subscription_id UUID PRIMARY KEY,
            callback_uri    VARCHAR NOT NULL,
            refusals        JSON NOT NULL,
            created_at      TIMESTAMP WITH TIME ZONE NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE safeguard_refusal (
            refusal_id   UUID PRIMARY KEY,
            occurred_at  TIMESTAMP WITH TIME ZONE NOT NULL,
            invoker_id   VARCHAR NOT NULL,
            requested_by VARCHAR,
            code         VARCHAR NOT NULL,
            detail       VARCHAR,
            notified     BOOLEAN NOT NULL
        )
    """)
    op.execute("CREATE INDEX ix_safeguard_refusal_occurred_at ON safeguard_refusal (occurred_at)")
    op.execute("CREATE INDEX ix_safeguard_refusal_invoker_id ON safeguard_refusal (invoker_id)")


def downgrade() -> None:
    """Drop both tables."""
    op.execute("DROP TABLE safeguard_refusal")
    op.execute("DROP TABLE safeguard_subscription")
