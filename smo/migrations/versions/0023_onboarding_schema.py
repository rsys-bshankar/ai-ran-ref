"""Onboarding's tables move into the schema `onboarding` (PR-DB-2.5), the pilot of per-module schemas.

`application_package`, `artifact` and `package_usage_registration` move with `ALTER TABLE ... SET SCHEMA`: the data, the indexes, the
constraints between them and their rows stay as they are. A view of the same name stays behind in `public`, so the previous release's code,
which names the tables without a schema and finds them through the default search path, still runs on the new schema, and keeps running
for connections it opened before the migration (docs/adr/0001-schema-migrations.md: a release runs on the schema of the next). The views
are updatable (one table, no join), so the old code reads and writes through them. They are dropped by the revision that follows the next
release, with the rest of the compatibility surface.

The new code finds the tables first: its role's search path is `onboarding, public` (`scripts/db_roles.py`), so an unqualified name resolves
to the table, not the view. A service that still connects as the owner (no per-module role) finds the view, which works the same.

Revision ID: 0023
Revises: 0022
"""
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

SCHEMA = "onboarding"
TABLES = ("application_package", "artifact", "package_usage_registration")


def upgrade() -> None:
    """Move Onboarding's three tables into the schema `onboarding` and leave an updatable view of the same name in `public` for each."""
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
    for table in TABLES:
        op.execute(f"ALTER TABLE public.{table} SET SCHEMA {SCHEMA}")
        # The compatibility view: one table and no join, so it is updatable and the previous release's unqualified reads and writes keep working. Dropped by 0029.
        op.execute(f"CREATE VIEW public.{table} AS SELECT * FROM {SCHEMA}.{table}")


def downgrade() -> None:
    """Drop the views and move the tables back to `public`, then drop the schema (which fails if anything else is left in it)."""
    for table in TABLES:
        op.execute(f"DROP VIEW IF EXISTS public.{table}")
        op.execute(f"ALTER TABLE {SCHEMA}.{table} SET SCHEMA public")
    op.execute(f"DROP SCHEMA IF EXISTS {SCHEMA}")
