"""Tenant and region authorization (PR-SEC-10, steps SEC-10.2 and SEC-10.3): the scope of a target and the scope claim of a caller.

Nullable columns on existing tables, nothing else (`docs/adr/0005-tenant-region-authorization.md`):

  ran_nf_oam.managed_entity.region, .tenant     where a managed element is and whom it belongs to; both plus an index each (a scoped list filters on them)
  ran_nf_oam.rapp_action_approval.requester_scope   the requester's scope claim (JSON) when the request was parked, re-checked against the target when it is approved
  sme.invoker_registration.authz_scope          the scope claim of an invoker (JSON `{"regions": [...], "tenants": [...]}`); introspection returns it
  rapp_mgmt.rapp_instance.authz_scope           the claim an instance was created with (JSON); put on its invoker at SME, kept by an upgrade
  rapp_mgmt.rapp_instance_version.previous_authz_scope   what an upgrade or rollback restores

Expand only: the previous release's code neither reads nor writes any of them, and every row already there gets NULL, which means "no region", "no tenant" and "no claim":
an unscoped caller (every caller after the upgrade, until an operator sets a claim) behaves exactly as before, and a scoped caller meets a target without a region or tenant
as a target it may not touch (fail closed).

Revision ID: 0032
Revises: 0031
"""
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE ran_nf_oam.managed_entity ADD COLUMN IF NOT EXISTS region VARCHAR")
    op.execute("ALTER TABLE ran_nf_oam.managed_entity ADD COLUMN IF NOT EXISTS tenant VARCHAR")
    op.execute("CREATE INDEX IF NOT EXISTS ix_managed_entity_region ON ran_nf_oam.managed_entity (region)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_managed_entity_tenant ON ran_nf_oam.managed_entity (tenant)")
    op.execute("ALTER TABLE ran_nf_oam.rapp_action_approval ADD COLUMN IF NOT EXISTS requester_scope JSON")
    op.execute("ALTER TABLE sme.invoker_registration ADD COLUMN IF NOT EXISTS authz_scope JSON")
    op.execute("ALTER TABLE rapp_mgmt.rapp_instance ADD COLUMN IF NOT EXISTS authz_scope JSON")
    op.execute("ALTER TABLE rapp_mgmt.rapp_instance_version ADD COLUMN IF NOT EXISTS previous_authz_scope JSON")


def downgrade() -> None:
    op.execute("ALTER TABLE rapp_mgmt.rapp_instance_version DROP COLUMN IF EXISTS previous_authz_scope")
    op.execute("ALTER TABLE rapp_mgmt.rapp_instance DROP COLUMN IF EXISTS authz_scope")
    op.execute("ALTER TABLE sme.invoker_registration DROP COLUMN IF EXISTS authz_scope")
    op.execute("ALTER TABLE ran_nf_oam.rapp_action_approval DROP COLUMN IF EXISTS requester_scope")
    op.execute("DROP INDEX IF EXISTS ran_nf_oam.ix_managed_entity_tenant")
    op.execute("DROP INDEX IF EXISTS ran_nf_oam.ix_managed_entity_region")
    op.execute("ALTER TABLE ran_nf_oam.managed_entity DROP COLUMN IF EXISTS tenant")
    op.execute("ALTER TABLE ran_nf_oam.managed_entity DROP COLUMN IF EXISTS region")
