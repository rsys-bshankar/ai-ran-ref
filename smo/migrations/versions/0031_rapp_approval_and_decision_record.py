"""Human approval of rApp actions and the decision record (PR-AI-11.1, PR-AI-13.1).

RAN NF OAM gets four tables (all in its schema `ran_nf_oam`, `ran-nf-oam/app/models.py` is the model):

  rapp_approval_policy      one row per rApp (invoker id) whose config jobs wait for a human, with the timeout and what a lapsed request becomes
  rapp_action_approval      one rApp action waiting for, or given, a decision; holds the whole write request until it is approved
  approval_subscription     who is told (through the outbox) that a decision is needed
  rapp_decision_record      why an rApp acted: inputs reference, model version, rationale, job, approver; carries a hash that is also written to
                            the shared audit chain (`audit_log`)

rApp Management gets two nullable columns: `rapp_instance.approval_policy` (the policy an instance was created with, JSON) and
`rapp_instance_version.previous_approval_policy` (what an upgrade or rollback restores).

Expand only: new tables the previous release never reads or writes, and nullable columns it ignores. No row exists until an operator creates an instance with an
approval policy or sets one, so an upgrade changes nothing for a running rApp. The tables of a module with a database role need no grant of their own: the role's
default privileges on its schema cover them (`scripts/db_roles.py`); RAN NF OAM's role is additionally granted the shared `audit_log` and `audit_head`
(`migrations/db_roles.json`) to chain a decision record.

Revision ID: 0031
Revises: 0030
"""
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the four approval and decision-record tables of RAN NF OAM with their indexes, and add `approval_policy` and `previous_approval_policy` to the rApp Management tables; every statement tolerates a rerun."""
    op.execute("""
        CREATE TABLE IF NOT EXISTS ran_nf_oam.rapp_approval_policy (
            invoker_id      VARCHAR PRIMARY KEY,
            timeout_seconds INTEGER NOT NULL DEFAULT 3600,
            on_timeout      VARCHAR NOT NULL DEFAULT 'EXPIRE',
            set_by          VARCHAR,
            updated_at      TIMESTAMP WITH TIME ZONE NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS ran_nf_oam.rapp_action_approval (
            approval_id      UUID PRIMARY KEY,
            invoker_id       VARCHAR NOT NULL,
            requested_by     VARCHAR NOT NULL,
            status           VARCHAR NOT NULL,
            request          JSON NOT NULL,
            managed_elements JSON NOT NULL,
            change_count     INTEGER NOT NULL,
            created_at       TIMESTAMP WITH TIME ZONE NOT NULL,
            expires_at       TIMESTAMP WITH TIME ZONE NOT NULL,
            on_timeout       VARCHAR NOT NULL,
            decided_by       VARCHAR,
            decided_at       TIMESTAMP WITH TIME ZONE,
            decision_reason  VARCHAR,
            job_id           UUID,
            refusal_code     VARCHAR,
            correlation_id   VARCHAR,
            CONSTRAINT rapp_action_approval_status_check CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED', 'EXPIRED', 'REFUSED'))
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_rapp_action_approval_invoker_id ON ran_nf_oam.rapp_action_approval (invoker_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_rapp_action_approval_status ON ran_nf_oam.rapp_action_approval (status)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_rapp_action_approval_created_at ON ran_nf_oam.rapp_action_approval (created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_rapp_action_approval_job_id ON ran_nf_oam.rapp_action_approval (job_id)")
    op.execute("""
        CREATE TABLE IF NOT EXISTS ran_nf_oam.approval_subscription (
            subscription_id UUID PRIMARY KEY,
            callback_uri    VARCHAR NOT NULL,
            created_at      TIMESTAMP WITH TIME ZONE NOT NULL
        )
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS ran_nf_oam.rapp_decision_record (
            decision_id      UUID PRIMARY KEY,
            occurred_at      TIMESTAMP WITH TIME ZONE NOT NULL,
            invoker_id       VARCHAR NOT NULL,
            requested_by     VARCHAR NOT NULL,
            disposition      VARCHAR NOT NULL,
            job_id           UUID UNIQUE,
            approval_id      UUID,
            action_id        VARCHAR,
            inputs_ref       VARCHAR,
            model_version    VARCHAR,
            rationale        VARCHAR,
            decided_by       VARCHAR,
            decided_at       TIMESTAMP WITH TIME ZONE,
            managed_elements JSON NOT NULL,
            change_count     INTEGER NOT NULL,
            correlation_id   VARCHAR,
            content_hash     VARCHAR(64) NOT NULL,
            audit_seq        BIGINT
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_rapp_decision_record_occurred_at ON ran_nf_oam.rapp_decision_record (occurred_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_rapp_decision_record_invoker_id ON ran_nf_oam.rapp_decision_record (invoker_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_rapp_decision_record_approval_id ON ran_nf_oam.rapp_decision_record (approval_id)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_rapp_decision_record_model_version ON ran_nf_oam.rapp_decision_record (model_version)")
    op.execute("ALTER TABLE rapp_mgmt.rapp_instance ADD COLUMN IF NOT EXISTS approval_policy JSON")
    op.execute("ALTER TABLE rapp_mgmt.rapp_instance_version ADD COLUMN IF NOT EXISTS previous_approval_policy JSON")


def downgrade() -> None:
    """Drop the two columns and the four tables, in reverse order; the approval history and decision records are lost."""
    op.execute("ALTER TABLE rapp_mgmt.rapp_instance_version DROP COLUMN IF EXISTS previous_approval_policy")
    op.execute("ALTER TABLE rapp_mgmt.rapp_instance DROP COLUMN IF EXISTS approval_policy")
    op.execute("DROP TABLE IF EXISTS ran_nf_oam.rapp_decision_record")
    op.execute("DROP TABLE IF EXISTS ran_nf_oam.approval_subscription")
    op.execute("DROP TABLE IF EXISTS ran_nf_oam.rapp_action_approval")
    op.execute("DROP TABLE IF EXISTS ran_nf_oam.rapp_approval_policy")
