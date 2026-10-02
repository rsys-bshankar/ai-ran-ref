#!/usr/bin/env python3
"""Brings the SMO database to the latest schema revision (PR-OPS-1).

    python scripts/migrate.py                 upgrade to head (the default)
    python scripts/migrate.py --revision 0001 upgrade to a given revision
    python scripts/migrate.py --current       print the database's revision and exit

The URL comes from `SMO_DATABASE_URL` (or the `*_FILE` forms), as for every service.

A database that has the schema but no `alembic_version` table was created from `migrations/001_init.sql` directly
(docker compose's initdb does this): it is stamped at the baseline revision first, so it is upgraded from there,
never re-created. An empty database runs the baseline.
"""

import argparse
import sys
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--revision", default="head")
    parser.add_argument("--current", action="store_true")
    args = parser.parse_args()
    engine = create_engine(resolve_database_url())
    with engine.connect() as connection:
        if args.current:
            print(current_revision(connection) or "(none)")
            return 0
        legacy = is_legacy(connection)
        revision = migrate(connection, args.revision)
        print(f"{'stamped the existing schema at ' + BASELINE + ' and ' if legacy else ''}upgraded to {revision}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
