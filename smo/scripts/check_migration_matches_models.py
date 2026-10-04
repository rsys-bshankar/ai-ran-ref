#!/usr/bin/env python3
"""Compares the real Postgres schema, migrated to the head revision,
against every module's own SQLAlchemy ORM model definitions, table by
table and column by column.

HISTORY.md §2: two real Postgres-only bugs (`rapp_instance`'s
`pending_upgrade_instance_id` entirely missing from the migration;
`oauth_client_id` wrongly `NOT NULL` there even though the ORM/app code
sets it back to NULL) were only ever caught by luck — the
migration-postgres CI job checked table *count* (>= 30), never columns,
and SQLite's own unit tests build their schema from the ORM models
directly, never from this file, so neither test path could ever have
caught either bug. This is that column-level check, made automatic —
the same "catch real structural drift automatically instead of relying
on human diligence" philosophy already used for the OpenAPI spec
drift-check and the docker-compose-config CI job.

Scope is deliberately narrow: column *presence* and *nullability* only
— exactly the two bug classes already found, not a full type-equality
check (SQLAlchemy's generic String()/Text()/ARRAY-with-SQLite-variant
types don't map to a single canonical Postgres type name, so a strict
type comparison would produce noise unrelated to any real bug).

Requires a live Postgres migrated to the head revision with
`python scripts/migrate.py` (SMO_DATABASE_URL), which this script also
verifies: a model change needs a revision (migrations/versions/, PR-OPS-1,
docs/adr/0001-schema-migrations.md), and the check fails until the database,
migrated to head, has the column. Run from the migration-postgres CI job.
"""

import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect

if not os.environ.get("SMO_DATABASE_URL"):  # no default credentials (PR-DB-1); checked before the app modules import smo_shared.db
    sys.exit("SMO_DATABASE_URL is not set: point it at a Postgres migrated with `python scripts/migrate.py` "
             "(see smo/CLAUDE.md, step 4).")

SMO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SMO_ROOT / "tests_integration"))
sys.path.insert(0, str(SMO_ROOT / "sdk"))  # the sample rApp imports the AI Runtime SDK

from loader import load_app_module  # noqa: E402

from smo_shared.db import Base  # noqa: E402
import smo_shared.idempotency  # noqa: E402,F401  (registers idempotency_key on the shared Base)
import smo_shared.module_identity  # noqa: E402,F401  (registers module_identity on the shared Base)
import smo_shared.single_runner  # noqa: E402,F401  (registers periodic_run on the shared Base)
import smo_shared.outbox  # noqa: E402,F401  (registers notification_outbox on the shared Base)
import smo_shared.audit  # noqa: E402,F401  (registers audit_log and audit_head)

# Every module that persists to Postgres via smo_shared.db.Base — the two
# mocks (mock-near-rt-ric, mock-o1-adaptor) have no models and aren't part
# of migrations/001_init.sql, so they're deliberately excluded here.
ALL_MODULES = [
    "r1-termination", "sme", "dme", "onboarding", "rapp-mgmt", "ran-nf-oam",
    "a1-related", "nfo", "focom", "aimgf", "mlmr", "mllf", "ran-analytics", "mdaf",
    "intent-service", "so-smos", "sa-smos",
    "samples/energy-saving-rapp",  # Wave 10.1: the reference rApp's own tables
    "samples/mobility-optimization-rapp",  # Wave 10.2
    "samples/coverage-optimization-rapp",  # Wave 10.3
    "samples/traffic-steering-rapp",  # Wave 10.4
]


def check_at_head(engine) -> None:
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    config = Config(str(SMO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(SMO_ROOT / "migrations"))
    head = ScriptDirectory.from_config(config).get_current_head()
    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    if current != head:
        sys.exit(f"the database is at revision {current!r}, the migration head is {head!r}: "
                 "run `python scripts/migrate.py` first")


def main() -> None:
    for module_dir in ALL_MODULES:
        load_app_module(module_dir)  # side effect: registers that module's tables on the shared Base

    database_url = os.environ["SMO_DATABASE_URL"]
    engine = create_engine(database_url)
    inspector = inspect(engine)
    check_at_head(engine)
    real_tables = set(inspector.get_table_names())

    errors = []
    for table in Base.metadata.sorted_tables:
        if table.name not in real_tables:
            errors.append(f"table {table.name!r}: declared by the ORM but missing from the migrated schema")
            continue
        real_columns = {c["name"]: c for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name not in real_columns:
                errors.append(f"{table.name}.{column.name}: declared by the ORM but missing from the migrated schema")
                continue
            real_nullable = bool(real_columns[column.name]["nullable"])
            if real_nullable != bool(column.nullable):
                errors.append(
                    f"{table.name}.{column.name}: ORM says nullable={column.nullable}, "
                    f"schema says nullable={real_nullable}"
                )

    if errors:
        print(f"Schema mismatches between the migrated schema and the ORM models ({len(errors)}):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    print(f"OK: {len(Base.metadata.sorted_tables)} tables checked against real Postgres, "
          f"all ORM-declared columns present with matching nullability.")


if __name__ == "__main__":
    main()
