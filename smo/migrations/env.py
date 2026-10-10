"""The Alembic environment: the one SMO schema, one revision history (docs/adr/0001-schema-migrations.md).

No autogenerate: the SQLAlchemy models are not the schema's source of truth here (the SQL revisions in `versions/` are), and `scripts/check_migration_matches_models.py` is what compares the two,
so `target_metadata` is None. A caller that already holds a connection (the tests, `scripts/migrate.py`) passes it as `config.attributes["connection"]`; otherwise the database is the one
`smo_shared.db.resolve_database_url()` names. Alembic imports this file for every command and it runs the migrations at import time, as Alembic's own template does; it holds no
other logic. Rules for writing a revision: `smo/CLAUDE.md`, "Schema changes are revisions".
"""

from alembic import context
from sqlalchemy import create_engine, pool

from smo_shared.db import resolve_database_url

config = context.config


def run_migrations_online() -> None:
    """Run the revisions in the configured range against a live database, in one transaction.

    Uses the connection in `config.attributes["connection"]` when the caller gave one (the caller owns it; it is not closed here), else opens one from `resolve_database_url()` on an engine without a
    pool, which is closed when the migration ends. Raises whatever a revision raises; the transaction is then rolled back by Alembic.
    """
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(resolve_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=None)
        with context.begin_transaction():
            context.run_migrations()


run_migrations_online()
