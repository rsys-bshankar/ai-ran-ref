"""Alembic environment: the one SMO schema, one history (docs/adr/0001-schema-migrations.md).

No autogenerate: the SQLAlchemy models are not the schema's source of truth here (the SQL revisions are), and
`scripts/check_migration_matches_models.py` is what compares the two. A caller that already holds a connection
(the tests, `scripts/migrate.py`) passes it as `config.attributes["connection"]`.
"""

from alembic import context
from sqlalchemy import create_engine, pool

from smo_shared.db import resolve_database_url

config = context.config


def run_migrations_online() -> None:
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
