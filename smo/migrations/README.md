# migrations

The schema is this Alembic history (`docs/adr/0001-schema-migrations.md`). The rules for adding a revision are in `smo/CLAUDE.md`, "Schema
changes are revisions": a revision is additive within a MINOR release (expand), and removing or renaming something takes a second release
(contract). The CI job "Previous release's code runs on the new schema" (`scripts/check_previous_release_code.sh`) is what enforces it: it runs
whenever a pull request changes this directory. `0029` is the first contract revision: it drops what `0023` to `0025` kept for the release before.
