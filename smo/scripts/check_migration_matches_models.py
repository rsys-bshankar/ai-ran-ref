#!/usr/bin/env python3
"""Compares migrations/001_init.sql's real, applied Postgres schema
against every module's own SQLAlchemy ORM model definitions, table by
table and column by column.

OPEN_ITEMS.md section 2: two real Postgres-only bugs (`rapp_instance`'s
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

Requires a live Postgres with migrations/001_init.sql already applied
(SMO_DATABASE_URL) — run from the migration-postgres CI job, which
already has both.
"""

import os
import sys
from pathlib import Path

from sqlalchemy import create_engine, inspect

SMO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SMO_ROOT / "tests_integration"))

from loader import load_app_module  # noqa: E402

from smo_shared.db import Base  # noqa: E402

# Every module that persists to Postgres via smo_shared.db.Base — the two
# mocks (mock-near-rt-ric, mock-o1-adaptor) have no models and aren't part
# of migrations/001_init.sql, so they're deliberately excluded here.
ALL_MODULES = [
    "r1-termination", "sme", "dme", "onboarding", "rapp-mgmt", "ran-nf-oam",
    "a1-related", "nfo", "focom", "ai-ml-workflow", "ran-analytics",
    "intent-service", "so-smos", "sa-smos",
]


def main() -> None:
    for module_dir in ALL_MODULES:
        load_app_module(module_dir)  # side effect: registers that module's tables on the shared Base

    database_url = os.environ.get("SMO_DATABASE_URL", "postgresql+psycopg://smo:smo@localhost:5432/smo")
    engine = create_engine(database_url)
    inspector = inspect(engine)
    real_tables = set(inspector.get_table_names())

    errors = []
    for table in Base.metadata.sorted_tables:
        if table.name not in real_tables:
            errors.append(f"table {table.name!r}: declared by the ORM but missing from the migration")
            continue
        real_columns = {c["name"]: c for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name not in real_columns:
                errors.append(f"{table.name}.{column.name}: declared by the ORM but missing from the migration")
                continue
            real_nullable = bool(real_columns[column.name]["nullable"])
            if real_nullable != bool(column.nullable):
                errors.append(
                    f"{table.name}.{column.name}: ORM says nullable={column.nullable}, "
                    f"migration says nullable={real_nullable}"
                )

    if errors:
        print(f"Schema mismatches between migrations/001_init.sql and the ORM models ({len(errors)}):")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    print(f"OK: {len(Base.metadata.sorted_tables)} tables checked against real Postgres, "
          f"all ORM-declared columns present with matching nullability.")


if __name__ == "__main__":
    main()
