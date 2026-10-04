#!/usr/bin/env python3
"""Brings the SMO database to the latest schema revision (PR-OPS-1).

    python scripts/migrate.py                 upgrade to head (the default)
    python scripts/migrate.py --revision 0001 upgrade to a given revision
    python scripts/migrate.py --downgrade -1  reverse one revision (or to a revision id); take a backup first
    python scripts/migrate.py --current       print the database's revision and exit
    python scripts/migrate.py --wait SECONDS  wait until the database is at (or past) this image's head revision, up to SECONDS; exit 1 if it is not
                                              (what a Helm install's init containers run, so a pod never starts on an older schema, PR-OPS-3.2)

The URL comes from `SMO_DATABASE_URL` (or the `*_FILE` forms), as for every service.

A database that has the schema but no `alembic_version` table was created from `migrations/001_init.sql` directly
(docker compose's initdb does this): it is stamped at the baseline revision first, so it is upgraded from there,
never re-created. An empty database runs the baseline.
"""

import argparse
import sys
import time
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect

SMO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SMO_ROOT / "shared"))

from smo_shared.db import resolve_database_url  # noqa: E402

BASELINE = "0001"
# a table every baseline database has: its presence without alembic_version means "legacy, stamp me"
LEGACY_MARKER_TABLE = "service_profile"


def alembic_config(connection) -> Config:
    config = Config(str(SMO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(SMO_ROOT / "migrations"))
    config.attributes["connection"] = connection
    return config


def current_revision(connection) -> str | None:
    return MigrationContext.configure(connection).get_current_revision()


def is_legacy(connection) -> bool:
    tables = set(inspect(connection).get_table_names())
    return "alembic_version" not in tables and LEGACY_MARKER_TABLE in tables


def migrate(connection, revision: str = "head") -> str | None:
    """Upgrades `connection`'s database to `revision`; returns the revision it is then at."""
    config = alembic_config(connection)
    if is_legacy(connection):
        command.stamp(config, BASELINE)
        connection.commit()
    command.upgrade(config, revision)
    connection.commit()
    return current_revision(connection)


def schema_is_current(db_revision: str | None, image_revisions: list[str]) -> bool:
    """Whether a database at `db_revision` can serve code whose revision history is `image_revisions` (head first): it is at that head, or past it
    (a revision this image does not know is a later one: the previous release's code runs on a newer schema, by the expand/contract rule)."""
    if db_revision is None or not image_revisions:
        return False
    return db_revision not in image_revisions or db_revision == image_revisions[0]


def image_revisions(connection) -> list[str]:
    from alembic.script import ScriptDirectory
    return [r.revision for r in ScriptDirectory.from_config(alembic_config(connection)).walk_revisions()]


def wait_for_schema(seconds: float, interval: float = 3.0) -> bool:
    """Blocks until the database answers and is at this image's head (or past it); False when `seconds` pass first."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            engine = create_engine(resolve_database_url())
            with engine.connect() as connection:
                if schema_is_current(current_revision(connection), image_revisions(connection)):
                    return True
        except Exception as exc:                  # the database is not up yet, or the migration has not made its tables: keep waiting
            print(f"waiting for the database: {type(exc).__name__}", flush=True)
        if time.monotonic() >= deadline:
            return False
        time.sleep(interval)


def downgrade(connection, revision: str) -> str | None:
    """Reverses to `revision` (a revision id, or `-1` for one step); returns the revision the database is then at."""
    command.downgrade(alembic_config(connection), revision)
    connection.commit()
    return current_revision(connection)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--revision", default="head")
    parser.add_argument("--downgrade", metavar="REVISION", help="reverse to REVISION (-1: one step) instead of upgrading")
    parser.add_argument("--current", action="store_true")
    parser.add_argument("--wait", type=float, metavar="SECONDS", help="wait for the database to be at this image's head instead of migrating it")
    args = parser.parse_args()
    if args.wait is not None:
        ready = wait_for_schema(args.wait)
        print("the schema is current" if ready else f"the schema was not current after {args.wait:g} s", flush=True)
        return 0 if ready else 1
    engine = create_engine(resolve_database_url())
    with engine.connect() as connection:
        if args.current:
            print(current_revision(connection) or "(none)")
            return 0
        if args.downgrade:
            print(f"downgraded to {downgrade(connection, args.downgrade) or '(none)'}")
            return 0
        legacy = is_legacy(connection)
        revision = migrate(connection, args.revision)
        print(f"{'stamped the existing schema at ' + BASELINE + ' and ' if legacy else ''}upgraded to {revision}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
