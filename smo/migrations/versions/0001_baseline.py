"""Baseline: the consolidated schema of `migrations/001_init.sql`.

The revision runs that file unchanged, so there is one definition of the baseline and a fresh database and a
database that was created from the file by hand (docker compose's initdb does) reach the same schema: the second
is *stamped* at this revision instead of upgraded (`scripts/migrate.py`).

Revision ID: 0001
Revises:
"""
from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

BASELINE_SQL = Path(__file__).resolve().parent.parent / "001_init.sql"


def upgrade() -> None:
    """Create the whole baseline schema by running `001_init.sql` unchanged: every module's tables as the consolidated file defines them, and the shared tables the file ends with."""
    # Straight to the DBAPI cursor, with no parameters: the file is many statements, and both `op.execute(text(...))`
    # (reads the `:` in a CHECK constraint as a bind parameter) and `exec_driver_sql` (psycopg reads the `%` of a
    # `NOT LIKE '%:%'` as a placeholder) would try to interpret it.
    with op.get_bind().connection.cursor() as cursor:
        cursor.execute(BASELINE_SQL.read_text())


def downgrade() -> None:
    """Not supported: raises NotImplementedError, because dropping the whole schema is a restore, not a downgrade (`scripts/db_restore.sh`)."""
    raise NotImplementedError("the baseline is the first revision: restore a backup (scripts/db_restore.sh) instead")
