"""Two-person approval of an rApp's action (opt-in; follow-up to PR-AI-11 and PR-AI-13).

RAN NF OAM's approval tables gain columns, no new table:

  rapp_approval_policy.required_approvals    1 or 2: how many different people must approve a request of this rApp (default 1: one approval, as before)
  rapp_action_approval.required_approvals    the number the policy asked for when the request was parked (default 1)
  rapp_action_approval.approvals             the approvals given so far, a JSON list of {by, at, reason}; NULL while there are none
  rapp_decision_record.approvers             who approved, when the request needed two; NULL otherwise

Expand only. The two `required_approvals` columns are NOT NULL with a default of 1 (so a row written by the previous release's code, which names neither, reads as
"one approval", and the previous release's inserts keep working); the other two are nullable and ignored by the previous release. A CHECK keeps the number to 1 or
2. No existing row changes meaning, and no policy asks for two until an operator sets `requiredApprovals: 2` on one. No status is added, so the CHECK on
`rapp_action_approval.status` (revision 0031) is untouched.

Revision ID: 0036
Revises: 0035
"""
from alembic import op

revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ran_nf_oam.rapp_approval_policy ADD COLUMN IF NOT EXISTS required_approvals INTEGER NOT NULL DEFAULT 1")
    op.execute("ALTER TABLE ran_nf_oam.rapp_approval_policy DROP CONSTRAINT IF EXISTS rapp_approval_policy_required_approvals_check")
    op.execute("ALTER TABLE ran_nf_oam.rapp_approval_policy ADD CONSTRAINT rapp_approval_policy_required_approvals_check CHECK (required_approvals BETWEEN 1 AND 2)")
    op.execute("ALTER TABLE ran_nf_oam.rapp_action_approval ADD COLUMN IF NOT EXISTS required_approvals INTEGER NOT NULL DEFAULT 1")
    op.execute("ALTER TABLE ran_nf_oam.rapp_action_approval DROP CONSTRAINT IF EXISTS rapp_action_approval_required_approvals_check")
    op.execute("ALTER TABLE ran_nf_oam.rapp_action_approval ADD CONSTRAINT rapp_action_approval_required_approvals_check CHECK (required_approvals BETWEEN 1 AND 2)")
    op.execute("ALTER TABLE ran_nf_oam.rapp_action_approval ADD COLUMN IF NOT EXISTS approvals JSON")
    op.execute("ALTER TABLE ran_nf_oam.rapp_decision_record ADD COLUMN IF NOT EXISTS approvers JSON")


def downgrade() -> None:
    op.execute("ALTER TABLE ran_nf_oam.rapp_decision_record DROP COLUMN IF EXISTS approvers")
    op.execute("ALTER TABLE ran_nf_oam.rapp_action_approval DROP COLUMN IF EXISTS approvals")
    op.execute("ALTER TABLE ran_nf_oam.rapp_action_approval DROP CONSTRAINT IF EXISTS rapp_action_approval_required_approvals_check")
    op.execute("ALTER TABLE ran_nf_oam.rapp_action_approval DROP COLUMN IF EXISTS required_approvals")
    op.execute("ALTER TABLE ran_nf_oam.rapp_approval_policy DROP CONSTRAINT IF EXISTS rapp_approval_policy_required_approvals_check")
    op.execute("ALTER TABLE ran_nf_oam.rapp_approval_policy DROP COLUMN IF EXISTS required_approvals")
