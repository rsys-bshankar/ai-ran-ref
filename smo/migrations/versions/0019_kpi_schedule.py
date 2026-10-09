"""`kpi_schedule` (PR-MSG-4): publish a KPI to DME on a timer, run by the RAN NF OAM worker.

One new table, so the previous release (which does not know it) is unaffected. `ran-nf-oam/app/models.py` is the model.

Revision ID: 0019
Revises: 0018
"""
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create `kpi_schedule`: the timer that publishes a KPI to DME, with its last run and status."""
    op.execute("""
        CREATE TABLE kpi_schedule (
            schedule_id         VARCHAR PRIMARY KEY,
            kpi                 VARCHAR NOT NULL,
            interval_seconds    INTEGER NOT NULL,
            lookback_seconds    INTEGER NOT NULL,
            group_by            VARCHAR NOT NULL,
            managed_element_ref VARCHAR,
            cell_id             VARCHAR,
            enabled             BOOLEAN NOT NULL,
            last_run_at         TIMESTAMP WITH TIME ZONE,
            last_status         VARCHAR,
            last_detail         VARCHAR,
            updated_at          TIMESTAMP WITH TIME ZONE NOT NULL
        )
    """)


def downgrade() -> None:
    """Drop `kpi_schedule`."""
    op.execute("DROP TABLE kpi_schedule")
