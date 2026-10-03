# Changelog

All notable changes to the SMO. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning and how a release is cut: `docs/RELEASES.md`.
Entries are written for an operator: what changed in behaviour, configuration or the schema. The design record is `HISTORY.md`; what is still open is `OPEN_ITEMS.md`.

No release has been tagged yet. Everything below is unreleased.

## [Unreleased]

### Added
- CM change history: every dispatched write records the values it replaced and the values it wrote (`cm_snapshot`, schema revision `0004`); `GET /managed-entities/{ref}/config-history` lists them. Each write now makes one extra read of the NF first (up to 30 s on an unresponsive one); `RAN_NF_OAM_CM_SNAPSHOTS=false` turns it off (MGT-1.1–1.4).
- `dryRun: true` on `POST /config-jobs`: every check runs, nothing is sent or stored, and the response says per change whether it would pass (MGT-3).
- NETCONF over SSH to an O1 adaptor: register it with `transport: ssh` and `adaptorUri` `ssh://user@host[:port]`; CM writes and `GET /managed-entities/{ref}/config` then use RFC 6242 sessions. Set `NETCONF_SSH_KNOWN_HOSTS` and `NETCONF_SSH_PASSWORD` (or `_FILE`) / `NETCONF_SSH_KEY_FILE` for RAN NF OAM. Schema revision `0003` adds `o1_adaptor_endpoint.transport` (default `http-mock`) (SB-1.1–1.3).
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
- `PATCH /alarms/{id}/ack` and `/clear` return 404 `ALARM_NOT_FOUND` for an unknown alarm (they failed with a 500), and `new_state` must be `ACKNOWLEDGED` or `UNACKNOWLEDGED` (422 otherwise) (MGT-8.1).
- **CM writes are checked against each leaf's type and constraints before anything is sent.** A write with an out-of-range number (`localPortNumber` 70000), the wrong type, a string over its length or off its pattern, or too many decimals is refused with 422 `SCHEMA_VALIDATION_FAILED` and a reason, for vendors whose data model comes from the bundled WG10 / WG5 YANG or the 3GPP NR NRM descriptors, or from a descriptor you load; before, only unknown attributes and enum values were refused (SB-5).
- DME's type-change, offer-termination and job-push notifications are written to the transactional outbox in the same transaction as the change and sent after it commits, so a crash no longer loses them; delivery is at least once, so a consumer may see one twice after a crash (#MSG-1.5).
- SME's CAPIF event notifications (service and invoker events) use the same outbox, with the same at-least-once delivery (MSG-1.6).
- AI/ML Workflow's job-completion and model-performance notifications use the outbox too, committed together with the completion or report they announce (MSG-1.7).
- A1 Related, FOCOM (inventory, alarm and performance) and MDAF notifications use the outbox as well. An MDAF report, its subscriber notifications, threshold state and request deliveries are now one transaction (MSG-1.8).
- Intent Service (RMIH, report and autonomy-operator notifications) and RAN NF OAM (PM-file ready) use the outbox, which completes it: every notification to a registered destination, except the DME stop-job DELETE, is now durable. An autonomy dispatch and the Intent it creates are one transaction (MSG-1.9).
- The bundled O-RAN WG10 and WG5 YANG descriptors now include the attributes of the 3GPP common groupings (`id`, `userLabel`, the EP and managed-function attributes), so a CM write to those classes may set them; four port-number attributes are `integer` instead of `any` (SB-3).
- **The schema is created and upgraded by a `migrate` service**, which every service waits for; Postgres no longer mounts `001_init.sql`. A kept volume is stamped and upgraded on the next `docker compose up -d --build`; an empty one is built from the baseline. Schema revision `0002` adds the `notification_outbox` table (additive).
- **Database credentials have no default.** Services refuse to start without `SMO_DATABASE_URL`; compose reads the password from `secrets/db_password` (run `scripts/init_secrets.sh` first). A Postgres volume created with the old default password keeps it: recreate the volume (#199, #205).
- Containers run as a non-root user with all capabilities dropped; NFO no longer runs privileged and no longer mounts the Docker socket. Volumes created by an earlier stack are root-owned: recreate them (#203).
- The Dockerfile's plain-text uvicorn access line is off; the structured access log replaces it (#207).
- The migration CI job and `CLAUDE.md` step 4 create the schema with `scripts/migrate.py` instead of applying the SQL file directly (#209).
- The hello-world sample rApp and its CSAR are removed. The demo runbook (§0–§23), the integration tests and the GUI's onboard-form hint use the Energy Saving package instead.

### Upgrade notes
- `docker compose up -d --build` runs the migrations (`docker compose logs migrate`); outside compose run `python scripts/migrate.py`. A database created from `001_init.sql` by an earlier stack is stamped at `0001` first. Take a backup (`scripts/db_backup.sh`) before upgrading; `python scripts/migrate.py --downgrade -1` reverses one revision.

[Unreleased]: https://github.com/rsys-bshankar/ai-ran-ref/commits/main
