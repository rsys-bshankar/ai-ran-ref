"""Two changes the volume lane (PR-V-6) asked for: an index on `pm_file.managed_element_ref`, and a creation time on `lcm_operation`.

The performance files of one managed element (the query behind the element's PM file list) scanned the whole table: 27 ms at half a million
files in the volume lane (`.github/workflows/smo-db-volume.yml`, `scripts/db_volume_check.py`), growing with every file. `alarm` already had the same index
(`idx_alarm_me`).

A deployment's operation history was listed with no order at all (the table had nothing to order it by), so with the lists now paged in a stable order (`smo_shared.pagination`) its
order would have been that of random ids. `lcm_operation.created_at` (default now(); rows already there all get the migration time) gives the history its order.

No data is changed. The previous release's code runs on the new schema: the index changes no query's answer, and the new column has a default, so its inserts, which do not name it, still work.

Revision ID: 0027
Revises: 0026
"""
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS ix_pm_file_managed_element_ref ON ran_nf_oam.pm_file (managed_element_ref)")
    op.execute("ALTER TABLE nfo.lcm_operation ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT now()")


def downgrade() -> None:
    op.execute("ALTER TABLE nfo.lcm_operation DROP COLUMN IF EXISTS created_at")
    op.execute("DROP INDEX IF EXISTS ran_nf_oam.ix_pm_file_managed_element_ref")
