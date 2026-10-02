# Open items

Items still open in the Phase 1 SMO reference build, checked against the code. What was
decided and built is in [`HISTORY.md`](HISTORY.md). IDs keep their original numbering (`OI-…` from the former
`OPEN_ITEMS.md`, `SA-…` from the former `SPEC_AUDIT.md`, `W…` from the wave plan).

Each item in sections 1–4: what is missing, why it matters, suggested approach. Section 5 is the tier-1 production-readiness backlog, split into independently pickable blocks.

## 1. Design decisions without an answer

- **OI-1-weighted-triggers** — `WEIGHTED_TRIGGERS` group-retrain propagation raises
  `NotImplementedError` (`aimgf/app/statemachine.py`). Needs real per-model noise-floor data; a
  weighting invented without it would be arbitrary. Approach: collect breach statistics from
  `MLMFSubscription` performance reports, then design the weighting (LLD §4.4 revisit trigger 3).
- **OI-1-alarm-storm** — No alarm-storm correlation algorithm in `ran-nf-oam/`;
  `correlation_group` is a coarse string. No audited O-RAN-SC repo implements one either.
  Approach: wait for real alarm traces; start with time-window + topology (`neighbourRefs`) grouping.
- **OI-1-a1-ml** — A1-ML operations are out of scope (A1 Related LLD §0); schema dormant. Revisit only
  if that scope decision changes.
- **OI-6.1-runtime-gate** — `RuntimeLifecycle` transitions (Deploy/Activate/Scale/Terminate, call
  flow 17) have no operator approval beyond the `MODEL_NOT_CERTIFIED` guard. Whether the OI-6.1
  gate should extend to them is undecided. Approach: if yes, reuse the self-loop governance-event
  pattern (`APPROVE_DEPLOY`-style event + flag) through `POST /models/{id}/advance`.

## 2. Platform gaps

- **OI-7-nfo-scale-size** — Runtime scaling takes no target size: `POST /nfo/deployments/{id}/scale` has no
  replica or resource argument, so AIMgF `runtime/scale` cannot ask for one. Approach: add `replicas` /
  `resources` to the NFO scale call and the AIMgF scale request, and pass the manifest's runtime profile bounds.
- **OI-5-a1-scope** — `subscriptionScope` OWN/OTHERS is treated as ALL; no subscriber identity is
  tracked. Approach: record the subscriber's rApp id (from the R1 token) and compare with
  `creator_id`.
- **OI-5-a1-ric-inventory** — Policy types are the hardcoded `KNOWN_POLICY_TYPES`; `policySchema` is
  a placeholder; no `GET /rics`. Approach: fetch types and schemas from the Near-RT RIC (A1-P
  `GET /policytypes`) and model a RIC inventory if more than one RIC is introduced.

## 3. Spec conformance still open

### RAN NF OAM
- **SA-RANOAM-8 (streaming)** — File reporting is built (`POST /pm-files`, `GET /files`,
  `notifyFileReady`). TS 28.532 streaming data reporting is not: there is no streaming transport,
  and `delivery_method=stream` stays a registration (MDAF's `STREAMING` is likewise recorded only,
  `SA-MDA-5`). Approach: a streaming transport shared by RAN NF OAM and MDAF, when a consumer needs one.
- **SA-RANOAM-4 / SA-O1-1 (containment)** — DN refs are parsed and validated and the IOC class is
  taken from the last RDN, but `managedElementRef` is still a flat registry key and there is no DN
  containment tree (the accepted D-9 deviation).
- **SA-RANOAM-1 (reach)** — MSAC guards CM writes only. Reads (`GET .../config`) and the other write
  routes are not evaluated. Approach: reuse `msac.authorize` per route.

Closed in this wave: SA-RANOAM-1 (TS 28.319 Identity / Role / AccessRule, per-sub-change evaluation),
SA-RANOAM-2 (`accessScope`, `scope` kept as an alias), SA-RANOAM-6-severity (`PerceivedSeverity`,
`INDETERMINATE`, upper-case `perceivedSeverity`).

### FOCOM (O2IMS)
- **SA-FOCOM-6 (performance depth)** — `FILE` / `STREAM` performance reporting, `PerformanceMeasurementStore`
  retention and the `reportInterval` / `heartbeatInterval` schedule are not built; records are ingested, not
  collected. Approach: a collector and a file writer when FOCOM talks to a real O-Cloud.
- **SA-FOCOM-7 (real clusters)** — `ProvisioningRequest` is fulfilled at the model level (a `NodeCluster` row);
  nothing is deployed on an O-Cloud, so `PENDING` / `PROGRESSING` / `FAILED` are never observed. Approach:
  drive a real DMS asynchronously and report phases.

Closed in this wave: SA-FOCOM-2 (Location, OCloudSite, pool links, inline resources), SA-FOCOM-6
(AlarmEventRecord, AlarmSubscription, performance records / jobs / NOTIFICATION subscriptions), SA-FOCOM-7
(Artifacts, Cluster, Infrastructure, ProvisioningRequest resources), SA-FOCOM-9 (closed resource types, seeded,
`POST /resource-types`, auto-registration behind `FOCOM_AUTO_REGISTER_RESOURCE_TYPES`).

### MLMR (TS 29.482)
- **SA-MLMR-6 (location)** — `accessReqs.location` is stored, not enforced: no requester location exists.
  Approach: take a location from the invoker's registration if the platform ever models one.
- **SA-MLMR-7 (phases)** — AIMgF writes `phaseInfo.phase` at training start (`IN_TRAINING` /
  `IN_RETRAINING`) and success (`TRAINED`) only; validation and deployment do not write `VALIDATED` /
  `DEPLOYED`. Approach: write the phase from the lifecycle FSM transitions in `_fire_model_event`.
- **SA-MLMR-1 (spec edges)** — the `MLModel` `anyOf` and the forward-compatible free-string enum
  values are not honoured; a model cannot be created from a profile (no type / version in
  `mlModelInfo`).

Closed in this wave: SA-MLMR-1 (`/storages`, profiles), SA-MLMR-6 (`storeDiscReqs` enforced for
discovery and download), SA-MLMR-7 (`phaseInfo.trainingInfo.baseModelId` lineage from AIMgF),
SA-MLMR-8 (`usageReqs`), SA-MLMR-9 (whole-object `filt-criteria` discovery).

### Intent Service (TS 28.312)
- None open. SA-INTENT-partial is closed: the 8 value datatypes are structure-checked, `ValueRangeType`
  is enforced for generic targets and contexts, and `ReportingCondition` is validated in
  `intentReportControl` (`intent-service/app/ts28312_datatypes.py`).

### SME / O1 vendor models
- **SA-O1-4 (common modules)** — the WG10 / WG5 descriptors (`o-ran-wg10-o1nrm`, `o-ran-wg5-du-mp`,
  `o-ran-wg5-cu-mp`, `o-ran-wg10-wg5`) are bundled and generated by `scripts/ingest_yang_schema.py`, but the
  3GPP common YANG modules they import (`_3gpp-common-top`, `-managed-function`, `-yang-types`, the EP / RRM
  policy groupings) are not in `specs/`, so the attributes those groupings contribute (`id`, `userLabel`,
  `EP_Common`, ...) are missing and listed under `unresolved` in each descriptor. Approach: add the 3GPP YANG to
  `specs/` and regenerate. WG4 O-RU M-plane YANG is not ingested.

## 4. Test coverage

- **OI-4** — Coverage is uneven. By `def test_` count today the shallowest suites are `mllf` (5),
  `ran-analytics` (13), `mock-o1-adaptor` (14), `so-smos` (15), `mock-near-rt-ric` (16) and
  `r1-termination` (18). Approach: add route-level tests to `mllf` first (its routes are the
  CERTIFIED gate in every rApp deployment); the others were last surveyed as near-complete.

## 5. Production readiness (tier-1 operator deployment)

Sections 1–4 are about the reference build's own completeness. This section is the gap between that
build and a tier-1 operator deployment. It is split into small blocks that can be **picked
independently**, so a team can take the first slice of an area (for example stateless services) without
committing to the whole area (for example HA).

**How to read an item**

`PR-<area>-<n> — title` `[size]` then *What*, *Done when* (testable), *Needs* (hard prerequisites only; `none`
means it can start today) and, where useful, *Standalone value* (what you gain even if you stop there).

- **Size**: `S` ≤ 2 days, `M` ≤ 2 weeks, `L` > 2 weeks or needs external infrastructure or vendor access.
- **Needs** lists hard dependencies only. Nothing is implied by item order within an area.
- Findings below were checked against the code at the time of writing (a grep for background tasks, module
  state, locking, pooling). Items marked **(verify)** rest on something not yet confirmed.
- When an item is picked up, move its ID into `HISTORY.md` when closed, as for sections 1–4.

### 5.0 Area map and suggested first slices

| Area | Prefix | Theme | Earliest useful slice |
|---|---|---|---|
| Stateless / scale-out | `PR-ST` | Any replica can serve any request | ST-1 … ST-6 (all `S`/`M`, no infra) |
| Database | `PR-DB` | Pooling, per-module isolation, retention | DB-1, DB-2, DB-3 |
| Async messaging | `PR-MSG` | Durable notifications and jobs | MSG-1, MSG-2 (no broker needed) |
| Security | `PR-SEC` | Transport, secrets, identity, hardening | SEC-1, SEC-4, SEC-8 |
| Observability | `PR-OBS` | Health, metrics, traces, logs | OBS-1 … OBS-4 |
| Packaging / ops | `PR-OPS` | Migrations, Helm, releases | OPS-1, OPS-2 |
| High availability | `PR-HA` | Redundancy and DR | after ST + DB + MSG |
| Southbound | `PR-SB` | Real O1 / A1 / O2 | SB-1, SB-5, SB-9 |
| Management functions | `PR-MGT` | CM / FM / PM depth | MGT-1 … MGT-4 |
| Northbound / OSS | `PR-NB` | OSS/BSS, slicing, federation | NB-1, NB-2 |
| AI/ML platform | `PR-AI` | Engines, MLOps, safety | AI-1, AI-2, AI-7 |
| rApp ecosystem | `PR-RAPP` | Signing, sandboxing, certification | RAPP-1, RAPP-2 |
| GUI | `PR-GUI` | NOC-grade console | GUI-1, GUI-2 |
| Standards / compliance | `PR-STD` | Conformance and privacy | STD-1 |
| Quality engineering | `PR-QA` | Load, chaos, upgrade tests | QA-1, QA-2 |

**Dependency spine** (everything else is independent of it): `ST` → `DB-2` → `HA-*`, and `MSG-1/2` → `MSG-3/4` →
`HA-4`. Security, observability and packaging items do not depend on `ST`, `DB` or `MSG`.

### 5.1 Stateless / scale-out (`PR-ST`)

Current state, checked: no `create_task`, `BackgroundTasks`, `Thread` or scheduler in any `*/app` module, so
services are request-driven. The only process state is `R1Client`'s per-process token cache and invoker
identity (`shared/smo_shared/r1_client.py`), an `lru_cache` of the vendor registry (`ran-nf-oam/app/vendors.py`),
the GUI BFF's per-process login lockout, and the module-level dicts in the two mocks (test doubles, out of scope).
What is missing is proof, guardrails and the replica-safety items below.

- **PR-ST-1 — Statelessness audit and CI guard** `[S]`
  *What*: confirm and record that no module keeps request-visible state in-process; add a CI check (ruff rule or
  a small AST test) that fails on new module-level mutable containers or background threads in `*/app`, with an
  allowlist for the cases below. Also confirm how periodic or time-driven behaviour is triggered today
  (SA SMOS monitors, MDAF delivery, rApp Management supervision, `heartbeatInterval`) **(verify)**: if any needs a
  periodic tick, list it, because it will need `PR-ST-8`.
  *Done when*: audit table in the module READMEs or `docs/ARCHITECTURE.md`; guard runs in `smo-tests.yml`. *Needs*: none.
- **PR-ST-2 — Optimistic concurrency on FSM transitions** `[M]`
  *What*: no `with_for_update` or version column exists (checked), so two replicas can both move the same
  `ApplicationPackage`, `RAppInstance`, `NFDeployment` or model through one transition. Add a `row_version`
  column (or `SELECT … FOR UPDATE`) in `smo_shared/statemachine.py` and return 409 on conflict.
  *Done when*: a two-session test on Postgres shows exactly one winner per transition; migration included. *Needs*: none.
  *Standalone value*: removes lost-update bugs even with one replica and concurrent clients.
- **PR-ST-3 — Idempotency keys on create/command POSTs** `[M]`
  *What*: accept an `Idempotency-Key` header on creates and command routes (deploy, scale, advance, job start);
  store key + response hash for a TTL; replay returns the original result. Start with `rapp-mgmt`, `nfo`, `aimgf`, `intent-service`.
  *Done when*: retry of the same key creates one row; different payload under the same key is 422. *Needs*: none.
- **PR-ST-4 — Shared module identity across replicas** `[S]`
  *What*: each process self-onboards an SME invoker with a random label when `SMO_INVOKER_ID` is unset
  (checked), so N replicas and every restart create N more invoker registrations. Provision one identity per
  module (init job or secret), reuse it, and make onboarding idempotent per `MODULE`.
  *Done when*: scaling a module to 3 replicas adds zero registrations; SME shows one invoker per module. *Needs*: none.
- **PR-ST-5 — Shared session secrets and lockout for the GUI BFF** `[S]`
  *What*: session JWT signing key and login-lockout counters must not be per-process (BFF README §2.8). Load the key
  from config shared by all replicas; move lockout counters to the DB.
  *Done when*: a token minted by replica A verifies on B; failed logins on A count toward B's lockout. *Needs*: none (`PR-SEC-4` improves key handling).
- **PR-ST-6 — Pool, timeout and shutdown settings** `[S]`
  *What*: `create_engine` sets only `pool_pre_ping` (checked). Add env-driven `pool_size`, `max_overflow`,
  `pool_recycle`, `statement_timeout`, request timeouts, uvicorn graceful shutdown (`--timeout-graceful-shutdown`)
  and SIGTERM draining; keep `workers` configurable in the `Dockerfile`.
  *Done when*: defaults documented, load test shows no pool exhaustion at 3 replicas. *Needs*: none.
- **PR-ST-7 — Readiness vs liveness endpoints** `[S]`
  *What*: `/health` exists per module; add `/ready` (DB reachable, migrations at expected head, SME token obtainable) and keep
  `/health` as pure liveness.
  *Done when*: compose healthchecks and K8s probes use them. *Needs*: none. (Same item as `PR-OBS-1`; do once.)
- **PR-ST-8 — Single-runner guard for periodic work** `[M]` *(only if ST-1 finds periodic work)*
  *What*: a Postgres advisory-lock or lease helper in `smo_shared` so a timed task runs on one replica at a time.
  *Done when*: with 3 replicas a periodic task fires once per interval. *Needs*: `PR-ST-1`.
- **PR-ST-9 — Move the O1 retry sleep out of the request thread** `[S]`
  *What*: `ran-nf-oam` retries inline with `time.sleep` (`ran-nf-oam/app/main.py:72,94`), pinning a worker for the
  whole back-off. Bound the total retry budget and return `202` + status for slow writes, or hand the retry to `PR-MSG-2`.
  *Done when*: worst-case request time is bounded and documented. *Needs*: none (cleaner with `PR-MSG-2`).

### 5.2 Database (`PR-DB`)

- **PR-DB-1 — Remove default credentials** `[S]` — `smo/smo` Postgres user is in `docker-compose.yml` and the
  `SMO_DATABASE_URL` default (`shared/smo_shared/db.py`). Require the URL from the environment; fail fast if unset
  outside tests. *Done when*: no password literal in the repo outside tests/samples. *Needs*: none.
- **PR-DB-2 — Per-module schemas and roles** `[M]` — one shared schema today. Create a schema and a least-privilege role per
  module (`moduleScope` already partitions logically); cross-module reads only via R1. *Done when*: each module connects with a
  role that cannot read another module's tables; migration check still passes against Postgres. *Needs*: `PR-OPS-1` is helpful.
- **PR-DB-3 — Retention and partitioning for high-volume tables** `[M]` — PM records, alarms, audit log, webhook deliveries, MDAF
  reports. Add time partitioning and a retention job/config per table. *Done when*: documented retention default per table and a purge that is tested.
  *Needs*: none.
- **PR-DB-4 — Indexes and query plans for list endpoints** `[S]` — every list uses `LIMIT/OFFSET` + `COUNT(*)`. Add
  keyset pagination option and indexes for the top filters; record `EXPLAIN` for each. *Done when*: no seq-scan on the top 10 list routes at 1M rows. *Needs*: `PR-QA-1` data set.
- **PR-DB-5 — Connection pooling proxy** `[S]` — PgBouncer in compose/Helm for many replicas. *Needs*: `PR-ST-6`.
- **PR-DB-6 — Backup and restore drill** `[S]` — `pg_dump`/WAL archiving scripts, restore test in CI. *Done when*: restore of the demo data is verified by the runbook replay. *Needs*: none.
- **PR-DB-7 — Postgres HA** `[L]` — Patroni or a Postgres operator, synchronous replica, failover test. *Needs*: `PR-DB-5`, `PR-OPS-2`.

### 5.3 Async messaging and jobs (`PR-MSG`)

Webhooks go out best-effort and in-request through `smo_shared/webhook.py` (retry per `STANDARDS.md`: 0 retries for
notifications, 3 for others). A restart or a down subscriber loses events.

- **PR-MSG-1 — Transactional outbox for webhooks** `[M]` — write the notification row in the same DB transaction as the state change;
  a delivery step reads the outbox. Delivery can still be in-process at first. *Done when*: a crash between commit and send does not lose the
  notification. *Needs*: none. *Standalone value*: durability without any broker.
- **PR-MSG-2 — Delivery worker with retry/back-off and dead-letter** `[M]` — separate worker process (or the same image with a
  `ROLE=worker` flag) that drains the outbox, `SKIP LOCKED` for multi-replica safety, exponential back-off, DLQ table, metrics. *Needs*: `PR-MSG-1`.
- **PR-MSG-3 — Event bus adapter** `[M]` — an interface in `smo_shared` with Postgres (default) and Kafka/NATS implementations for internal events (alarm, PM, FSM state). *Needs*: `PR-MSG-1`.
- **PR-MSG-4 — Durable job runner for long operations** `[M]` — training, deploy, heal, scale, bulk CM as jobs with status, cancel and resume; reuse the worker from MSG-2. *Needs*: `PR-MSG-2`.
- **PR-MSG-5 — Webhook signing and delivery log API** `[S]` — HMAC signature header, per-subscription delivery history for operators. *Needs*: `PR-MSG-1`.
- **PR-MSG-6 — DNS-aware SSRF check at send time** `[S]` — webhook module accepts DNS rebinding as a residual risk today (module docstring); resolve and
  re-check the address at connect time, behind a flag that keeps unit tests working. *Needs*: none.

### 5.4 Security (`PR-SEC`)

`SECURITY.md` states the build is not hardened; `/bootstrap` and `/health` are unauthenticated; inter-service calls are plain HTTP.

- **PR-SEC-1 — TLS on the R1 gateway and the GUI** `[S]` — terminate TLS at R1 Termination / nginx with configurable certs. *Done when*: compose profile `tls` runs the runbook over HTTPS. *Needs*: none.
- **PR-SEC-2 — mTLS between services** `[M]` — client certs for module-to-module and module-to-Postgres. Either per-service or via a mesh (`PR-SEC-3`). *Needs*: `PR-SEC-1`.
- **PR-SEC-3 — Service mesh option** `[M]` — document and test Istio/Linkerd injection as an alternative to SEC-2. *Needs*: `PR-OPS-2`.
- **PR-SEC-4 — Secret management** `[M]` — all secrets (DB, JWT key, invoker secrets, adaptor credentials) read from files/Vault/KMS, never env literals in compose; rotation procedure. *Needs*: none.
- **PR-SEC-5 — Asymmetric token signing and JWKS** `[M]` — SME tokens and GUI sessions signed with RS256/ES256, key rotation, `kid`. GUI sessions are HS256 today. *Needs*: `PR-SEC-4`.
- **PR-SEC-6 — OIDC / SAML / LDAP login for the GUI** `[M]` — external IdP, group-to-role mapping onto `rbac.py`; keep local admin as break-glass. *Needs*: none.
- **PR-SEC-7 — MFA and server-side session revocation** `[M]` — TOTP or IdP-delegated; revocation list or short-lived tokens + refresh (BFF README §2.8 lists the gap). *Needs*: none.
- **PR-SEC-8 — Rate limiting and request size limits** `[S]` — per-invoker and per-IP limits at R1 Termination, body-size caps, 429 with `Retry-After`. *Needs*: none.
- **PR-SEC-9 — Authenticate `/bootstrap` consumers or restrict by network policy** `[S]` — today unauthenticated by design; at least document and provide NetworkPolicy/ingress rules. *Needs*: none.
- **PR-SEC-10 — Fine-grained authorization (policy engine)** `[L]` — per-tenant / per-region / per-object ABAC (OPA or equivalent) beyond route-level RBAC and rApp scopes. *Needs*: `PR-SEC-6` helpful.
- **PR-SEC-11 — Tamper-evident audit log and SIEM export** `[M]` — hash-chained audit rows, syslog/CEF/JSON export; extend audit to every module, not only the BFF. *Needs*: none.
- **PR-SEC-12 — Supply-chain evidence** `[S]` — SBOM per image, image signing (cosign), vulnerability scan gate in CI. Pinning by digest and hashed Python locks exist already. *Needs*: none.
- **PR-SEC-13 — Run as non-root, read-only filesystem, drop capabilities** `[S]` — container hardening in `Dockerfile`/compose; seccomp profile. *Needs*: none.
- **PR-SEC-14 — Pen-test and threat-model pass** `[M]` — STRIDE per interface (R1, O1, A1, O2, GUI). Output is a tracked finding list. *Needs*: at least SEC-1, SEC-4.
- **PR-SEC-15 — CSAR signature verification** `[M]` — see `PR-RAPP-1`; listed here for the security view.

### 5.5 Observability (`PR-OBS`)

No Prometheus, OpenTelemetry or `/metrics` usage exists in the code (checked). Correlation ids exist (`smo_shared/correlation.py`).

- **PR-OBS-1 — `/live` and `/ready`** `[S]` — same as `PR-ST-7`.
- **PR-OBS-2 — Structured JSON logging with correlation id** `[S]` — one logging config in `smo_shared`, fields: module, level, correlation id, route, status, duration. Redact tokens/secrets. *Needs*: none.
- **PR-OBS-3 — Prometheus metrics endpoint per module** `[M]` — request count/latency/error by route, DB pool, FSM transitions by type, webhook outcomes. Shared middleware. *Done when*: every module exposes `/metrics` (internal-only) and a sample dashboard JSON is committed. *Needs*: none.
- **PR-OBS-4 — W3C trace context and OpenTelemetry traces** `[M]` — carry `traceparent` across `R1Client` and R1 Termination; export OTLP; spans for DB and outbound calls. Keep the correlation header as a fallback. *Needs*: none.
- **PR-OBS-5 — Business metrics** `[S]` — rApp instances by state, packages by state, alarm counts, O1 write success rate, model lifecycle counts, action-approval wait time. *Needs*: `PR-OBS-3`.
- **PR-OBS-6 — Alert rules and SLO definitions** `[S]` — Prometheus rules for availability, latency, error budget, O1 write failures, webhook DLQ depth. *Needs*: `PR-OBS-3`.
- **PR-OBS-7 — Log shipping config** `[S]` — Loki/ELK/Fluent Bit examples. *Needs*: `PR-OBS-2`.
- **PR-OBS-8 — Operations runbooks** `[M]` — per-alert response, failure modes per module, backup/restore, certificate renewal. *Needs*: SLOs.
- **PR-OBS-9 — SMO self-monitoring view in the GUI** `[S]` — extend `GET /modules/status` with readiness, version and replica count. *Needs*: `PR-ST-7`.

### 5.6 Packaging, migrations and release (`PR-OPS`)

- **PR-OPS-1 — Real migration tooling** `[M]` — `migrations/001_init.sql` is the only schema file; a live system cannot upgrade from it. Adopt Alembic (or equivalent): baseline from `001`, one migration per change, an up/down test, and the existing migration-vs-models check pointed at the migration head. *Done when*: upgrading a DB created from the previous commit passes in CI. *Needs*: none.
- **PR-OPS-2 — Helm chart (or Kustomize) for all services** `[L]` — Deployments, Services, config, probes, resources, NetworkPolicy, PodDisruptionBudget, HPA-ready. Compose stays for the demo. *Needs*: `PR-ST-7` for probes.
- **PR-OPS-3 — Migration job as a pre-upgrade hook** `[S]` — run migrations once per release, not per replica. *Needs*: `PR-OPS-1`, `PR-OPS-2`.
- **PR-OPS-4 — Versioned releases and image tags** `[S]` — semver tags, changelog, image publish pipeline; `SECURITY.md` currently says there are no releases. *Needs*: none.
- **PR-OPS-5 — Rolling upgrade and rollback test** `[M]` — N→N+1 with mixed versions running; schema changes follow expand/contract. Document the rule in `CLAUDE.md`. *Needs*: `PR-OPS-1`, `PR-OPS-2`.
- **PR-OPS-6 — GitOps example (Argo CD / Flux)** `[S]` — environment overlays: lab, staging, prod. *Needs*: `PR-OPS-2`.
- **PR-OPS-7 — Configuration reference** `[S]` — one table of every environment variable per module with default and whether it is secret. *Needs*: none.
- **PR-OPS-8 — Feature flags** `[S]` — env-backed flag helper for gating incomplete items. *Needs*: none.
- **PR-OPS-9 — Resource requests/limits and sizing guide** `[S]` — numbers from the load test. *Needs*: `PR-QA-1`.

### 5.7 High availability and DR (`PR-HA`)

Deliberately later: each item assumes the stateless and database work above.

- **PR-HA-1 — Run each module with ≥2 replicas in a test** `[S]` — compose `deploy.replicas` or Helm values; replay the runbook. *Needs*: `PR-ST-2`, `PR-ST-4`, `PR-ST-5`.
- **PR-HA-2 — Zero-downtime rolling restart test** `[S]` — kill replicas during the runbook replay. *Needs*: `PR-HA-1`, `PR-ST-6`.
- **PR-HA-3 — Postgres failover test** `[M]` — see `PR-DB-7`. *Needs*: `PR-DB-7`.
- **PR-HA-4 — Delivery worker failover** `[S]` — kill a worker mid-batch; no loss, no duplicate beyond at-least-once. *Needs*: `PR-MSG-2`.
- **PR-HA-5 — Multi-zone placement rules** `[S]` — anti-affinity, topology spread. *Needs*: `PR-OPS-2`.
- **PR-HA-6 — Disaster recovery plan with RPO/RTO targets** `[M]` — cross-site backup shipping, restore order, GUI/R1 re-pointing. *Needs*: `PR-DB-6`.
- **PR-HA-7 — Geo-redundant active/standby** `[L]` — second site, replication, controlled failover. *Needs*: `PR-HA-6`.

### 5.8 Southbound realism (`PR-SB`)

Both southbound ends are mocks today (`mock-o1-adaptor`, `mock-near-rt-ric`); FOCOM and NFO are model-level.

**O1**
- **PR-SB-1 — Real NETCONF over SSH client** `[M]` — session management, `edit-config`/`get-config` with datastores, `lock`/`commit`/`discard`, timeouts, host-key verification. Today's client speaks to a mock's HTTP endpoint. *Done when*: runbook CM write passes against a NETCONF server (netopeer2 or vendor simulator). *Needs*: none.
- **PR-SB-2 — NETCONF credentials and trust store** `[S]` — per-adaptor secrets, SSH key or TLS client cert, rotation. *Needs*: `PR-SEC-4`, `PR-SB-1`.
- **PR-SB-3 — 3GPP common YANG ingest (`SA-O1-4`)** `[S]` — add the `_3gpp-common-*` modules to `specs/`, regenerate descriptors, clear the `unresolved` lists. *Needs*: none (spec files).
- **PR-SB-4 — WG4 O-RU M-plane YANG ingest** `[M]` — descriptors and validation for O-RU. *Needs*: `PR-SB-3` pattern.
- **PR-SB-5 — YANG-validated CM writes before send** `[M]` — use the generated descriptors to reject bad leaf types/ranges locally. *Needs*: none.
- **PR-SB-6 — MO containment tree (`SA-RANOAM-4`)** `[L]` — DN-keyed tree instead of flat `managedElementRef`; parent/child navigation, subtree reads. *Needs*: none.
- **PR-SB-7 — VES event receiver** `[M]` — O-RAN VES (fault, PM, heartbeat) over HTTP then Kafka; map to alarms and PM. *Needs*: none for HTTP, `PR-MSG-3` for Kafka.
- **PR-SB-8 — Streaming PM transport (`SA-RANOAM-8`)** `[L]` — streaming data reporting shared with MDAF. *Needs*: `PR-MSG-3`.
- **PR-SB-9 — Vendor adaptor conformance kit** `[M]` — a test pack a vendor adaptor must pass (CM, FM, PM, SW, discovery), run against the mock first. *Needs*: none.
- **PR-SB-10 — First real-vendor adaptor profile** `[L]` — one vendor's O-DU/O-CU YANG and quirks into the capability registry. *Needs*: vendor lab access, `PR-SB-1`.

**A1**
- **PR-SB-11 — RIC inventory model and `GET /rics`** `[M]` — (`OI-5-a1-ric-inventory`) store RICs, their URL, auth, health; policy types fetched from each. *Needs*: none.
- **PR-SB-12 — Subscriber identity for OWN/OTHERS scope** `[S]` — (`OI-5-a1-scope`). *Needs*: none.
- **PR-SB-13 — Real Near-RT RIC integration (O-RAN-SC simulator then real)** `[M]` — run the A1 flows against the SC `near-rt-ric-simulator` in CI. *Needs*: `PR-SB-11`.

**O2**
- **PR-SB-14 — O2-IMS client against a real inventory source** `[L]` — pull inventory from an O-Cloud IMS (or a simulator), reconcile into FOCOM. *Needs*: none.
- **PR-SB-15 — Async provisioning with real phases (`SA-FOCOM-7`)** `[L]` — `PENDING`/`PROGRESSING`/`FAILED` driven by a real cluster API. *Needs*: `PR-SB-14`, `PR-MSG-4`.
- **PR-SB-16 — O2-DMS / Kubernetes driver for NFO** `[L]` — instantiate, heal, scale (with target size, `OI-7`) against a K8s API. *Needs*: none; `PR-MSG-4` for long operations.
- **PR-SB-17 — FOCOM PM collector (`SA-FOCOM-6`)** `[M]` — scheduled collection, FILE/STREAM modes, retention. *Needs*: `PR-ST-8` or `PR-MSG-4`.

### 5.9 Management function depth (`PR-MGT`)

**Configuration management**
- **PR-MGT-1 — CM history, diff and rollback** `[M]` — snapshot before each write, diff API, rollback as a new write through the same MSAC path. *Needs*: none.
- **PR-MGT-2 — MSAC on reads and remaining routes (`SA-RANOAM-1` reach)** `[S]` — call `msac.authorize` per route. *Needs*: none.
- **PR-MGT-3 — Dry-run / pre-check** `[S]` — validate and return the would-be change set without sending. *Needs*: `PR-SB-5` improves it.
- **PR-MGT-4 — Change windows and approval workflow** `[M]` — schedule, approve, expire; reuse the governance-event pattern. *Needs*: none.
- **PR-MGT-5 — Bulk and staged (canary) CM rollout** `[M]` — wave sizes, health gate between waves, auto-halt. *Needs*: `PR-MGT-1`, `PR-MSG-4`.
- **PR-MGT-6 — Golden config and drift detection** `[M]` — compare desired vs actual per managed element. *Needs*: `PR-MGT-1`.
- **PR-MGT-7 — Plan management (TS 28.572)** `[M]` — plan objects and activation; spec file is in `specs/`. *Needs*: none.

**Fault management**
- **PR-MGT-8 — Alarm lifecycle** `[M]` — ack, unack, clear, comment, aging, suppression windows; per TS 28.111 notifications. *Needs*: none.
- **PR-MGT-9 — Alarm correlation v1 (`OI-1-alarm-storm`)** `[M]` — time-window + `neighbourRefs` grouping into `correlation_group`. *Needs*: `PR-MGT-8` helpful.
- **PR-MGT-10 — Topology-aware root cause** `[L]` — use TEIV/containment topology. *Needs*: `PR-MGT-9`, `PR-SB-6`.

**Performance management**
- **PR-MGT-11 — KPI engine (TS 28.554 style)** `[M]` — formula definitions over PM counters, per-cell/per-region aggregation, API. *Needs*: none.
- **PR-MGT-12 — PM file collection at scale** `[M]` — scheduled fetch, parsing, dedupe, backlog metrics. *Needs*: `PR-MSG-4`.
- **PR-MGT-13 — Trace and QoE management (TS 28.623 models)** `[L]` — trace job control and collection. *Needs*: `PR-SB-8`.

**Network lifecycle**
- **PR-MGT-14 — Zero-touch onboarding of a new managed element** `[L]` — PnP, initial config, SW baseline. *Needs*: `PR-SB-1`, `PR-MGT-1`.
- **PR-MGT-15 — Software management at scale** `[M]` — staged upgrade campaigns with rollback. *Needs*: `PR-MGT-5`.
- **PR-MGT-16 — Intent conflict detection and arbitration** `[L]` — detect two intents/rApps writing the same target; priority rules; surface in GUI. *Needs*: none.
- **PR-MGT-17 — SO SMOS saga semantics** `[M]` — compensation and resume instead of fail-fast. *Needs*: `PR-MSG-4`.
- **PR-MGT-18 — SA SMOS closed-loop SLA assurance** `[M]` — SLA objects, breach handling, escalation. *Needs*: `PR-MGT-11`.

### 5.10 Northbound and OSS/BSS (`PR-NB`)

- **PR-NB-1 — Northbound alarm forwarding** `[M]` — SNMP trap / Kafka / REST to the operator NOC. *Needs*: `PR-MGT-8`.
- **PR-NB-2 — Inventory / topology export** `[M]` — extend the TEIV export to a stable, versioned API and a CMDB sync job. *Needs*: none.
- **PR-NB-3 — TS 28.532 MnS producer facade** `[L]` — expose CM/FM/PM to an external consumer using the TS 28.532 shapes already in `specs/`. *Needs*: none.
- **PR-NB-4 — Network slice management objects** `[L]` — NSMF/NSSMF-style objects using TS 28.541/28.531 models. *Needs*: `PR-NB-3`.
- **PR-NB-5 — TM Forum Open API adaptors (TMF 641 / 921 / 633)** `[L]` — thin mapping layer; start with one. *Needs*: none.
- **PR-NB-6 — ONAP integration profile** `[M]` — document and test the SMO against ONAP SDN-R/DMaaP style flows. *Needs*: `PR-SB-7`.
- **PR-NB-7 — SMO-to-SMO federation** `[L]` — multi-domain trust and delegation. *Needs*: `PR-SEC-10`.

### 5.11 AI/ML platform depth (`PR-AI`)

- **PR-AI-1 — Pluggable training/validation/emulation executor interface** `[M]` — make the runtime job executors replaceable; keep today's behaviour as the default stub. *Needs*: none. *Standalone value*: unblocks every later AI item.
- **PR-AI-2 — Kubernetes Job executor for training** `[L]` — run a container as a training job, collect metrics/artifacts into MLMR. *Needs*: `PR-AI-1`, `PR-SB-16`.
- **PR-AI-3 — MLflow-compatible tracking and registry bridge** `[M]` — mirror MLMR models to/from MLflow. *Needs*: `PR-AI-1`.
- **PR-AI-4 — Inference serving adaptor (KServe / Triton)** `[L]` — `RuntimeLifecycle` deploys to a serving runtime, `OI-7` target size. *Needs*: `PR-SB-16`.
- **PR-AI-5 — Feature store interface** `[M]` — feature groups backed by an external store; online/offline reads. *Needs*: none.
- **PR-AI-6 — Data lake / time-series sink for PM** `[M]` — export DME/PM to Parquet/TSDB. *Needs*: `PR-MGT-12`.
- **PR-AI-7 — Model drift and performance monitoring** `[M]` — consume `MLMFSubscription` reports, drift metrics, auto-flag a model. *Needs*: none.
- **PR-AI-8 — Weighted retrain triggers (`OI-1-weighted-triggers`)** `[M]` — design from real breach data. *Needs*: `PR-AI-7`.
- **PR-AI-9 — Runtime lifecycle approval gate (`OI-6.1-runtime-gate`)** `[S]` — decision then implementation using the self-loop governance pattern. *Needs*: decision.
- **PR-AI-10 — Action safeguard layer** `[M]` — per-rApp bounds on CM writes (rate, magnitude, blast radius), kill switch, automatic revert on KPI regression. *Needs*: `PR-MGT-1`.
- **PR-AI-11 — Human-in-the-loop approval for rApp actions** `[M]` — autonomy mode already exists; add an approval queue with timeout policy. *Needs*: none.
- **PR-AI-12 — Simulation / digital-twin sandbox hook** `[L]` — route an rApp's writes to an emulator before production. *Needs*: `PR-AI-1`.
- **PR-AI-13 — Model explainability and decision audit** `[M]` — store inputs, model version and rationale per action. *Needs*: none.

### 5.12 rApp ecosystem (`PR-RAPP`)

- **PR-RAPP-1 — CSAR signing and verification** `[M]` — signed manifest, trusted publisher keys, reject unsigned in production mode. *Needs*: none.
- **PR-RAPP-2 — rApp runtime sandboxing and resource limits** `[M]` — CPU/memory/network policy per instance from the manifest runtime profile. *Needs*: `PR-SB-16`.
- **PR-RAPP-3 — Conformance test pack for third-party rApps** `[M]` — onboarding, lifecycle, R1 usage, heartbeat checks as an installable test tool. *Needs*: none.
- **PR-RAPP-4 — SDK for Java and Go** `[L]` — generated from `docs/openapi/`; one language first. *Needs*: none.
- **PR-RAPP-5 — Developer portal and API docs site** `[M]` — publish OpenAPI, call flows, packaging guide. *Needs*: none.
- **PR-RAPP-6 — rApp usage metering** `[S]` — per-rApp call and data volume counters. *Needs*: `PR-OBS-3`.
- **PR-RAPP-7 — Additional reference rApps** `[M each]` — anomaly detection, root cause, slice assurance. *Needs*: none.

### 5.13 GUI (`PR-GUI`)

- **PR-GUI-1 — Live updates (SSE/WebSocket) for alarms and states** `[M]` — replace polling. *Needs*: `PR-MSG-3` optional.
- **PR-GUI-2 — Alarm console** `[M]` — filter, ack, clear, comment, export. *Needs*: `PR-MGT-8`.
- **PR-GUI-3 — Topology and map view** `[L]` — containment and neighbour graph. *Needs*: `PR-SB-6`.
- **PR-GUI-4 — KPI dashboards** `[M]` — from the KPI engine. *Needs*: `PR-MGT-11`.
- **PR-GUI-5 — Region/tenant-scoped views** `[M]` — filter by assigned scope. *Needs*: `PR-SEC-10`.
- **PR-GUI-6 — Accessibility and localization pass** `[M]` — WCAG checks in CI, i18n scaffolding. *Needs*: none.
- **PR-GUI-7 — Approval inbox** `[S]` — central list of pending approvals (CM, models, rApp actions). *Needs*: `PR-MGT-4`, `PR-AI-11` for content.

### 5.14 Standards and compliance (`PR-STD`)

- **PR-STD-1 — Close open conformance items** `[S–M each]` — `SA-MLMR-1/6/7`, `SA-FOCOM-6/7`, `SA-RANOAM-1/4/8`, `SA-O1-4` (listed in §3). Cross-reference only; do not duplicate.
- **PR-STD-2 — Specification currency check** `[S]` — record the O-RAN and 3GPP release each spec in `specs/` is from; list newer releases and what changed for the SMO.
- **PR-STD-3 — O-RAN test and certification plan (OTIC)** `[M]` — map interfaces to O-RAN test specs, plan plugfest participation. *Needs*: `PR-SB-*`.
- **PR-STD-4 — Data privacy review (GDPR)** `[M]` — inventory personal data (GUI users, subscriber-derived PM), retention, erasure, access logging. *Needs*: `PR-DB-3`.
- **PR-STD-5 — Security assurance mapping (NESAS/SCAS, ISO 27001 controls)** `[M]` — control matrix against what exists. *Needs*: `PR-SEC-14`.
- **PR-STD-6 — Data residency and tenancy statement** `[S]` — where data lives, what leaves a site. *Needs*: none.

### 5.15 Quality engineering (`PR-QA`)

- **PR-QA-1 — Load generator and baseline** `[M]` — synthetic cells/PM/alarms at 1k, 10k, 100k scale; record throughput and latency per route. *Needs*: none. Gives the numbers sizing and HA items depend on.
- **PR-QA-2 — Contract tests from `docs/openapi/`** `[S]` — consumer-side checks for every cross-module call. *Needs*: none.
- **PR-QA-3 — Failure injection** `[M]` — kill DB, drop a service, slow a subscriber, during the runbook replay. *Needs*: `PR-HA-1` for replica cases.
- **PR-QA-4 — Upgrade/migration test in CI** `[S]` — previous release's DB upgraded to head. *Needs*: `PR-OPS-1`.
- **PR-QA-5 — Soak test** `[M]` — 24–72 h at baseline load; memory and pool leak detection. *Needs*: `PR-QA-1`, `PR-OBS-3`.
- **PR-QA-6 — Security tests in CI** `[S]` — authz matrix test across every route (no unauthenticated path except `/health`, `/bootstrap`). *Needs*: none.
- **PR-QA-7 — Coverage floor and shallow-suite work (`OI-4`)** `[S–M]` — start with `mllf` route tests. *Needs*: none.
- **PR-QA-8 — Real-simulator end-to-end lane** `[M]` — nightly job against the O-RAN-SC simulators and a NETCONF server. *Needs*: `PR-SB-1`, `PR-SB-13`.

### 5.16 Suggested first slices

Three example orderings. Pick one, or mix; the dependency spine in §5.0 is the only constraint.

1. **Replica-safe foundation (no new infrastructure)**: ST-1, ST-2, ST-4, ST-5, ST-6, ST-7, DB-1, OBS-2, OPS-7.
   Result: services can run N copies correctly, even if you still run one.
2. **Safe to expose**: SEC-1, SEC-4, SEC-8, SEC-13, SEC-12, DB-1, QA-6.
   Result: encrypted transport, no default secrets, rate-limited front door.
3. **Operable**: OBS-1…OBS-4, OPS-1, OPS-4, DB-6.
   Result: you can see, upgrade and restore it.

Then `MSG-1/2` (durable notifications), `SB-1/3/5` (real O1), `MGT-1/8` (CM history, alarm lifecycle), and only
afterwards `HA-*`.
