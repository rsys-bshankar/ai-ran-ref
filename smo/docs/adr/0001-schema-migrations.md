# ADR 0001 — Schema migrations: Alembic, one history

Status: accepted (`PR-OPS-1.1`). Date: 2026-10-02.

## Context

`migrations/001_init.sql` was the only schema file: a running system could not upgrade from it. Every module shares one Postgres
schema (`HISTORY.md` §2), and the SQLAlchemy models are compared with the migrated schema by
`scripts/check_migration_matches_models.py`. Docker compose creates the schema by mounting the file into the Postgres image's initdb
directory.

## Decision

1. **Alembic**, the standard SQLAlchemy migration tool (already the ORM in use), with revisions as plain files that run SQL
   (`op.execute`/`op.add_column` as suits) and a `downgrade` that actually reverses them.
2. **One history for the whole schema, not one per module.** The tables are one schema with foreign keys across module boundaries
   (an rApp instance row references a service profile row), so an upgrade is one ordered sequence applied once, by one job. Per-module
   histories would need a cross-module ordering anyway. Module ownership stays visible in the revision's message and file name.
3. **The baseline revision `0001` runs `migrations/001_init.sql` unchanged.** There is one definition of the baseline, the file
   docker compose already mounts. A fresh database runs it; a database created from the file by hand is **stamped** at `0001`
   (`scripts/migrate.py` does this when it finds the schema but no `alembic_version`), then upgraded like any other.
4. **No autogenerate.** Revisions are written by hand and reviewed; the models are not the source of truth for the schema, the
   revisions are. `check_migration_matches_models.py` is the safety net: it runs against a database migrated to head and fails when a
   model declares a table or column the migrated schema lacks, or a nullability differs, or the database is not at head.
5. **Alembic lives in `migrations/`** (`env.py`, `versions/`, beside `001_init.sql`) with `smo/alembic.ini`. The directory is not named
   `alembic/` so it cannot shadow the package. The URL comes from `SMO_DATABASE_URL` like every service.
6. **Migrations are run by a job, not by the services at start-up** (`OPS-1.5`, `OPS-3`): N replicas must not race to alter the schema.

## Consequences

- A schema change is a new file in `migrations/versions/` (`OPS-1.7` makes this a contributor rule).
- Rolling upgrades (`OPS-5`) need revisions that are compatible with the previous release: add before use, remove after.
- `001_init.sql` must never be edited once released; later changes are revisions on top of it.
- Not decided here: how the migrate job is wired into compose (`OPS-1.5`) and Helm (`OPS-3`), and the previous-commit upgrade test in CI (`OPS-1.6`).
