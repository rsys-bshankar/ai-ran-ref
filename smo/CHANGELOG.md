# Changelog

All notable changes to the SMO. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning and how a release is cut: `docs/RELEASES.md`.
Entries are written for an operator: what changed in behaviour, configuration or the schema. The design record is `HISTORY.md`; what is still open is `OPEN_ITEMS.md`.

No release has been tagged yet. Everything below is unreleased.

## [Unreleased]

### Added
- Structured JSON logs on stdout in every service, one access line per request with the route template, status and duration, secrets scrubbed; `LOG_LEVEL` (#207).
- `GET /metrics` in every service (Prometheus): request count and latency by method, route template and status. Container network only: R1 Termination and the TLS edge do not forward it (#208).
- Alembic schema migrations: baseline revision `0001` is `migrations/001_init.sql`; `python scripts/migrate.py` upgrades, and stamps a database that was created from the file (#209).
- Optional TLS edge (`docker compose --profile tls up`, nginx on `3443` and `8443`, development certificates from `scripts/make_dev_certs.sh`) (#206).
- Secrets inventory (`docs/SECRETS.md`), `*_FILE` variants for secrets, and the database password as a compose secret (`scripts/init_secrets.sh`) (#205).
- Request body cap (`R1_MAX_BODY_BYTES`) and a per-caller rate limit (`R1_RATE_PER_SECOND`, `R1_RATE_BURST`) at R1 Termination (#204).
- Database backup and restore scripts (`scripts/db_backup.sh`, `db_restore.sh`) (#200); slow-statement log in compose (`POSTGRES_SLOW_QUERY_MS`) (#201).
- Route-table authorisation walk: every backend route is refused without a valid token (#202).
- Liveness and readiness probes (`/live`, `/ready`; `/health` kept), single-runner guard for periodic work, idempotency keys on command routes, optimistic concurrency on lifecycle rows, one SME identity per module across replicas, connection pool limits and graceful shutdown (#190–#198).

### Changed
- **The schema is created and upgraded by a `migrate` service**, which every service waits for; Postgres no longer mounts `001_init.sql`. A kept volume is stamped and upgraded on the next `docker compose up -d --build`; an empty one is built from the baseline. Schema revision `0002` adds the `notification_outbox` table (additive).
- **Database credentials have no default.** Services refuse to start without `SMO_DATABASE_URL`; compose reads the password from `secrets/db_password` (run `scripts/init_secrets.sh` first). A Postgres volume created with the old default password keeps it: recreate the volume (#199, #205).
- Containers run as a non-root user with all capabilities dropped; NFO no longer runs privileged and no longer mounts the Docker socket. Volumes created by an earlier stack are root-owned: recreate them (#203).
- The Dockerfile's plain-text uvicorn access line is off; the structured access log replaces it (#207).
- The migration CI job and `CLAUDE.md` step 4 create the schema with `scripts/migrate.py` instead of applying the SQL file directly (#209).

### Upgrade notes
- `docker compose up -d --build` runs the migrations (`docker compose logs migrate`); outside compose run `python scripts/migrate.py`. A database created from `001_init.sql` by an earlier stack is stamped at `0001` first. Take a backup (`scripts/db_backup.sh`) before upgrading; `python scripts/migrate.py --downgrade -1` reverses one revision.

[Unreleased]: https://github.com/rsys-bshankar/ai-ran-ref/commits/main
