"""The data the console redesign asks of RAN NF OAM (PR-GUI-9.4, 9.5, 9.8): an acknowledge time on alarms, a site cluster on managed elements, and two indexes.

  ran_nf_oam.alarm.acknowledged_at          when the alarm was acknowledged (set by `PATCH /alarms/{id}/ack`, cleared again by an un-ack); the mean
                                            time to acknowledge of `GET /alarms/stats` is computed from it
  ran_nf_oam.managed_entity.site_cluster    an operator's grouping of elements below the region (`PUT /managed-entities/{me}/site-cluster`), for the
                                            health map and the list filter
  ix_alarm_raised_at                        the time filters, the hourly buckets of `GET /alarms/counts?group_by=hour` and the keyset order of `GET /alarms`
  ix_managed_entity_site_cluster            the `site_cluster` filter and grouping

(`cleared_at`, the clear time, already exists since the initial schema.)

Expand only: two nullable columns the previous release neither reads nor writes, and two indexes that change no query's answer. No data is changed:
an alarm acknowledged before the upgrade keeps a NULL ack time (its acknowledge moment was never recorded; `changed_at` may be a later clear), so the mean
time to acknowledge covers acknowledgements made after the upgrade. The downgrade drops the indexes and the columns.

Revision ID: 0034
Revises: 0033
"""
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add `alarm.acknowledged_at` and `managed_entity.site_cluster` (both nullable), and the indexes `ix_alarm_raised_at` and `ix_managed_entity_site_cluster`; every statement tolerates a rerun."""
    op.execute("ALTER TABLE ran_nf_oam.alarm ADD COLUMN IF NOT EXISTS acknowledged_at TIMESTAMP WITH TIME ZONE")
    op.execute("ALTER TABLE ran_nf_oam.managed_entity ADD COLUMN IF NOT EXISTS site_cluster VARCHAR")
    op.execute("CREATE INDEX IF NOT EXISTS ix_alarm_raised_at ON ran_nf_oam.alarm (raised_at)")
    op.execute("CREATE INDEX IF NOT EXISTS ix_managed_entity_site_cluster ON ran_nf_oam.managed_entity (site_cluster)")


def downgrade() -> None:
    """Drop the two indexes and the two columns if they exist."""
    op.execute("DROP INDEX IF EXISTS ran_nf_oam.ix_managed_entity_site_cluster")
    op.execute("DROP INDEX IF EXISTS ran_nf_oam.ix_alarm_raised_at")
    op.execute("ALTER TABLE ran_nf_oam.managed_entity DROP COLUMN IF EXISTS site_cluster")
    op.execute("ALTER TABLE ran_nf_oam.alarm DROP COLUMN IF EXISTS acknowledged_at")
