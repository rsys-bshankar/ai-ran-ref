"""Notifications of the life-cycle flows, a software job timeout and the order of a rollback (PR-MGT-14.7, MGT-15.6, MGT-15.7).

RAN NF OAM gets one table and two columns (its schema `ran_nf_oam`, `ran-nf-oam/app/models.py` is the model):

  lifecycle_subscription                who is told, through the outbox, when an element's onboarding fails, a software campaign halts or its rollback fails
  software_campaign.job_timeout_seconds nullable: how long a wave's (or a rollback step's) software jobs may take before the sweep fails those still running
  software_campaign.rollback_order      'all' (every revert job at once, what 0033 did) or 'reverse' (the last wave first, the next when it has ended)

Expand only: a new table the previous release never reads, a nullable column it ignores and a column with a default ('all') that the previous release's INSERT does
not name, so a campaign it creates keeps the behaviour it had. No row exists until an operator subscribes, and every existing campaign has no timeout and rolls back
as before. The table of a module with a database role needs no grant of its own: the role's default privileges on its schema cover it (`scripts/db_roles.py`).

Revision ID: 0034
Revises: 0033
"""
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS ran_nf_oam.lifecycle_subscription (
            subscription_id UUID PRIMARY KEY,
            callback_uri    VARCHAR NOT NULL,
            events          JSON NOT NULL,
            created_at      TIMESTAMP WITH TIME ZONE NOT NULL
        )
    """)
    op.execute("ALTER TABLE ran_nf_oam.software_campaign ADD COLUMN IF NOT EXISTS job_timeout_seconds INTEGER")
    op.execute("""
        ALTER TABLE ran_nf_oam.software_campaign ADD COLUMN IF NOT EXISTS rollback_order VARCHAR NOT NULL DEFAULT 'all'
            CONSTRAINT software_campaign_rollback_order_check CHECK (rollback_order IN ('all', 'reverse'))
    """)


def downgrade() -> None:
    op.execute("ALTER TABLE ran_nf_oam.software_campaign DROP COLUMN IF EXISTS rollback_order")
    op.execute("ALTER TABLE ran_nf_oam.software_campaign DROP COLUMN IF EXISTS job_timeout_seconds")
    op.execute("DROP TABLE IF EXISTS ran_nf_oam.lifecycle_subscription")
