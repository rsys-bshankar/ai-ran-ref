"""An index for FOCOM's node utilisation reads (PR-GUI-9.8b).

  focom.ix_ocloud_performance_metric_resource_metric_collected    (resource_ref, metric_name, collected_at): the newest CPU_UTILIZATION and
                                                                  MEMORY_UTILIZATION record per resource (`GET /focom/resources/{id}/utilisation`
                                                                  and its batched form `GET /focom/utilisation`), and the `resource_ref` filter
                                                                  of `GET /focom/performance`

Expand only: one index that changes no query's answer; the previous release neither knows nor needs it. No data is changed. The downgrade drops it.

Revision ID: 0038
Revises: 0037
"""
from alembic import op

revision = "0038"
down_revision = "0037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create `ix_ocloud_performance_metric_resource_metric_collected`; tolerates a rerun."""
    op.execute("CREATE INDEX IF NOT EXISTS ix_ocloud_performance_metric_resource_metric_collected "
               "ON focom.ocloud_performance_metric (resource_ref, metric_name, collected_at)")


def downgrade() -> None:
    """Drop the index if it exists."""
    op.execute("DROP INDEX IF EXISTS focom.ix_ocloud_performance_metric_resource_metric_collected")
