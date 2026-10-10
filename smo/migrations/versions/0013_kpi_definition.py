"""`kpi_definition` (PR-MGT-11.1): KPIs as formulas over PM counters.

A new table, so the previous release (which does not know it) is unaffected. `ran-nf-oam/app/models.py` is the model.

Revision ID: 0013
Revises: 0012
"""
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create `kpi_definition`: a KPI's name, formula and the counters it reads."""
    op.execute("""
        CREATE TABLE kpi_definition (
            name        VARCHAR PRIMARY KEY,
            formula     VARCHAR NOT NULL,
            counters    JSON NOT NULL,
            unit        VARCHAR,
            description VARCHAR,
            updated_at  TIMESTAMP WITH TIME ZONE NOT NULL
        )
    """)


def downgrade() -> None:
    """Drop `kpi_definition`."""
    op.execute("DROP TABLE kpi_definition")
