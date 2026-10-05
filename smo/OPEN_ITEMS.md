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
- **SA-RANOAM-4 / SA-O1-1 (containment)** — DN refs are parsed and validated, the IOC class is
  taken from the last RDN, and the registry now has a containment tree (`managed_object`, `GET /managed-objects/{dn}/children`, PR-SB-6.1).
  `managedElementRef` is still a flat registry key (its root DN is `ManagedElement=<ref>`), and a model-based server fills the tree through
  `POST /managed-entities/{ref}/managed-objects/refresh` (PR-SB-6.2); a server without a model reports no objects.
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
- **SA-O1-4 (common modules)** — closed by `PR-SB-3`: the 3GPP common modules (`specs/MnS/yang-models`) are a
  library for the YANG generator and the WG10 / WG5 descriptors resolve completely. WG4 O-RU M-plane YANG is not
  ingested (`PR-SB-4`).

## 4. Test coverage

- **OI-4** — Coverage is uneven. By `def test_` count today the shallowest suites are `mllf` (5),
  `ran-analytics` (13), `mock-o1-adaptor` (14), `so-smos` (15), `mock-near-rt-ric` (16) and
  `r1-termination` (18). Approach: add route-level tests to `mllf` first (its routes are the
  CERTIFIED gate in every rApp deployment); the others were last surveyed as near-complete.

## 5. Production readiness (tier-1 operator deployment)

Sections 1–4 are about the reference build's own completeness. This section is the gap between that
build and a tier-1 operator deployment. It is written as **features made of small steps**, so a team can take
the first steps of a feature without committing to the whole feature (for example, stateless services from day 1
and HA much later).

**How to read it**

- A **feature** (`PR-ST-7`) is a capability. Its **steps** (`ST-7.1`, `ST-7.2`, …) are the pickable units. Cite a
  step as `PR-ST-7.3`.
- Every step is sized **≤ 2 days** and has a testable **Done when**. A step that cannot be said in one line of
  "Done when" has been split further.
- **Needs** lists hard prerequisites only (`–` means it can start today). Steps are listed in a sensible order, but
  only `Needs` is binding.
- **★** marks a step that gives value on its own if you stop right after it.
- **(verify)** marks a statement that was not confirmed against running code.
- Where a step touches a table, it includes its migration: a revision in `migrations/versions/` (`CLAUDE.md`, "Schema changes
  are revisions"), never an edit to `001_init.sql`.
- When a feature is complete, move its ID to `HISTORY.md`, as for sections 1–4.

### 5.0 Feature map

| Area | Prefix | Features |
|---|---|---|
| Stateless / scale-out | `PR-ST` | ST-7 readiness (schema check) · ST-8 single-runner (adoption) · ST-9 inline retry (move to job runner) |
| Database | `PR-DB` | DB-2 per-module schemas · DB-3 retention · DB-4 indexes/pagination · DB-5 pooler · DB-6 backup · DB-7 Postgres HA |
| Messaging and jobs | `PR-MSG` | MSG-1 outbox · MSG-2 delivery worker · MSG-3 event bus · MSG-4 job runner · MSG-5 signing/log · MSG-6 SSRF at send |
| Security | `PR-SEC` | SEC-1 edge TLS · SEC-2 mTLS · SEC-3 mesh · SEC-4 secrets · SEC-5 signing keys · SEC-6 OIDC · SEC-7 MFA/revocation · SEC-8 rate limits · SEC-9 bootstrap exposure · SEC-10 tenant/region authz · SEC-11 audit · SEC-12 supply chain · SEC-13 container hardening · SEC-14 threat model |
| Observability | `PR-OBS` | OBS-2 metrics · OBS-3 traces · OBS-4 business metrics · OBS-5 alerts/SLOs · OBS-6 log shipping · OBS-7 runbooks · OBS-8 self-monitoring |
| Packaging / ops | `PR-OPS` | OPS-1 migrations · OPS-2 Helm · OPS-3 migrate hook · OPS-4 releases · OPS-5 rolling upgrade · OPS-6 GitOps · OPS-7 config reference · OPS-8 flags · OPS-9 sizing · OPS-10 dev-sanity pipeline (Actions) · OPS-11 demo environment (Codespaces) |
| High availability | `PR-HA` | HA-1 replicas · HA-2 rolling restart · HA-3 DB failover · HA-4 worker failover · HA-5 placement · HA-6 DR · HA-7 geo |
| Southbound | `PR-SB` | SB-1 NETCONF/SSH · SB-2 adaptor credentials · SB-3 3GPP YANG · SB-4 WG4 YANG · SB-5 YANG validation · SB-6 containment · SB-7 VES · SB-8 streaming · SB-9 conformance kit · SB-10 vendor profile · SB-11 RIC inventory · SB-12 A1 scope · SB-13 RIC simulator lane · SB-14 O2-IMS client · SB-15 async provisioning · SB-16 K8s driver · SB-17 NFO scale size · SB-18 FOCOM PM collector |
| Management functions | `PR-MGT` | MGT-1 CM history/rollback · MGT-2 MSAC reach · MGT-3 dry-run · MGT-4 change windows · MGT-5 canary · MGT-6 drift · MGT-7 plan mgmt · MGT-8 alarm lifecycle · MGT-9 correlation · MGT-10 topology RCA · MGT-11 KPI engine · MGT-12 PM at scale · MGT-13 trace/QoE · MGT-14 zero-touch · MGT-15 SW campaigns · MGT-16 intent conflicts · MGT-17 SO saga · MGT-18 SLA assurance |
| Northbound | `PR-NB` | NB-1 alarm forwarding · NB-2 inventory export · NB-3 TS 28.532 facade · NB-4 slicing · NB-5 TM Forum · NB-6 ONAP · NB-7 federation |
| AI/ML | `PR-AI` | AI-1 executor protocol · AI-2 K8s training executor · AI-3 MLflow bridge · AI-4 serving adaptor · AI-5 feature store · AI-6 data sink · AI-7 drift · AI-8 weighted triggers · AI-9 runtime gate · AI-10 action safeguards · AI-11 approvals · AI-12 shadow mode · AI-13 decision audit |
| rApp ecosystem | `PR-RAPP` | RAPP-1 signing · RAPP-2 sandbox · RAPP-3 conformance pack · RAPP-4 Java/Go SDK · RAPP-5 portal · RAPP-6 metering · RAPP-7 new-rApp recipe |
| GUI | `PR-GUI` | GUI-1 live updates · GUI-2 alarm console · GUI-3 topology · GUI-4 KPI dashboards · GUI-5 scoped views · GUI-6 a11y/i18n · GUI-7 approval inbox |
| Standards / compliance | `PR-STD` | STD-1 close §3 items · STD-2 spec currency · STD-3 O-RAN test plan · STD-4 privacy · STD-5 assurance mapping · STD-6 residency |
| Quality | `PR-QA` | QA-1 load · QA-2 contract tests · QA-3 failure injection · QA-4 upgrade test · QA-5 soak · QA-6 authz matrix · QA-7 coverage · QA-8 simulator lane |

**Dependency spine** (everything else is independent of it): `DB-2` → `HA-3`; `MSG-1` → `MSG-2` →
`MSG-4`/`HA-4`; `OPS-1` → `OPS-3`/`OPS-5`; `OBS-2` → `OBS-4`/`OBS-5`.

### 5.1 Stateless / scale-out (`PR-ST`)

State today, checked in the code (audit closed as `PR-ST-1`, `HISTORY.md` §10): no `create_task`, `BackgroundTasks`, `Thread` or scheduler in any `*/app`
module. Process state is limited to `R1Client`'s token cache and invoker identity
(`shared/smo_shared/r1_client.py`), an `lru_cache` of the vendor registry (`ran-nf-oam/app/vendors.py`), the
GUI BFF's per-process login lockout, and module-level dicts in the two mocks (test doubles, out of scope).

#### PR-ST-7 — Readiness vs liveness (open: the schema check; also the base for `OBS-8`)

| Step | What | Done when | Needs |
|---|---|---|---|
| ST-7.4 | Check: schema at expected head (a function passed to `install_health`, as `database_check` is) | Mismatch → not ready | OPS-1.2 |

#### PR-ST-8 — Single-runner guard (adopted by the RAN NF OAM worker, `HISTORY.md` PR-MSG-4; open for each later periodic task: `SB-18.2`, `MGT-6.4`, `MGT-8.6`, `MGT-12.1`)

| Step | What | Done when | Needs |
|---|---|---|---|
| ST-8.3 | Adopt `run_once_per_interval` for each periodic task found | Per task: one firing per interval | – |

#### PR-ST-9 — Inline retry in the request thread (open: moving it out of the request)

RAN NF OAM still retries southbound writes with `time.sleep` inside the request (`ran-nf-oam/app/main.py`), now bounded by a time budget (worst case per sub-change in the module README).

| Step | What | Done when | Needs |
|---|---|---|---|
| ST-9.3 | Hand retries to the job runner so no `sleep` remains in a request path | Grep test: no `sleep` in `*/app` request code | MSG-4.5 |


### 5.2 Database (`PR-DB`)

#### PR-DB-2 — Per-module schemas and roles

| Step | What | Done when | Needs |
|---|---|---|---|
| DB-2.1 ★ | Table → owning-module map for all tables (≈120) in a checked-in file (done: `migrations/table_owners.json`, 134 tables) | File merged | – |
| DB-2.2 | CI test: every table in the migrated schema is declared by exactly one module's models (done: `tests_integration/test_table_owners.py` and `check_migration_matches_models.py`) | Test fails on an orphan table | DB-2.1 |
| DB-2.3 | List foreign keys that cross modules; each is a break of "modules talk only through R1" | List with a decision per FK (keep as ID reference without FK, or move) (done: 24, all decided "plain id", in the docstring of revision `0022`) | DB-2.1 |
| DB-2.4 | Replace cross-module FKs by plain ID columns, one module pair per PR (done in one revision, `0022`: dropping a constraint changes no data, and the pairs share the same reasoning) | Per PR: tests and migration check green | DB-2.3 |
| DB-2.5 | Pilot: `onboarding` tables in schema `onboarding`; `search_path` set by the service (done: revision `0023`; the search path is the role's own default, so the service sets nothing) | Module works; other modules unaffected | DB-2.2 |
| DB-2.6 | Pilot role `smo_onboarding` with rights only on its schema (done for compose and the Helm chart: `scripts/db_roles.py`, `tests_integration/test_db_roles.py`, `databaseRoles` in the chart) | Role cannot read another schema (test) | DB-2.5 |
| DB-2.7 | Repeat DB-2.5/2.6 for each remaining module (done for every module that uses the database: Onboarding, then ten in `0024`, then SME, DME, NFO, RApp Management, A1 Related, FOCOM, AIMgF, RAN NF OAM and R1 Termination in `0025`; MLLF and the mocks have no database) | Per module: runbook replay green | DB-2.6 |

#### PR-DB-3 — Retention

| Step | What | Done when | Needs |
|---|---|---|---|
| DB-3.1 ★ | Table of high-volume tables with proposed retention (alarm, PM records and files, audit, webhook/outbox, idempotency, MDAF reports) | Table in the module READMEs | – |
| DB-3.2 | Env-driven retention setting per table | Defaults documented | DB-3.1 |
| DB-3.3 | Purge command (script or admin route) for cleared alarms older than N days | Test deletes only eligible rows | DB-3.2 |
| DB-3.4 | Same for PM records and PM files (also remove the file on disk) | Same | DB-3.2 |
| DB-3.5 | Same for MDAF reports | Same | DB-3.2 |
| DB-3.6 | Same for GUI audit log, with an optional export-before-delete | Same | DB-3.2 |
| DB-3.7 | Schedule the purges (cron, K8s CronJob or `ST-8`) | Documented schedule | DB-3.3 |
| DB-3.8 | Time partitioning for the PM table | Old partition drops in one statement | OPS-1.4 |

#### PR-DB-4 — Indexes and pagination

| Step | What | Done when | Needs |
|---|---|---|---|
| DB-4.2 | Script: `EXPLAIN` the ten most used list routes against a seeded large table | Plans recorded | QA-1.2 |
| DB-4.3 | Add the missing indexes | No sequential scan on those routes at 1M rows | DB-4.2 |
| DB-4.4 | Keyset (cursor) pagination option in `pagination.py` (`?after=`), `LIMIT/OFFSET` stays default | Unit tests; one route adopts it | – |
| DB-4.5 | Adopt keyset on alarms, PM records, audit | Same | DB-4.4 |

#### PR-DB-5 — Connection pooler

| Step | What | Done when | Needs |
|---|---|---|---|
| DB-5.1 | PgBouncer service in a compose profile (done: profile `pooler`, CI job `compose-pooler`) | Runbook replay green through it | – |
| DB-5.2 | Check psycopg 3 prepared statements with transaction pooling; set the needed flag (done: `SMO_DB_POOLER`, `SMO_DB_PREPARE_THRESHOLD`, `scripts/pooler_check.py`) | No "prepared statement does not exist" errors | DB-5.1 |
| DB-5.3 | Document pool sizing across replicas (done: README section) | Section in README | – |

#### PR-DB-6 — Backup and restore

| Step | What | Done when | Needs |
|---|---|---|---|
| DB-6.2 | CI job: backup, wipe, restore, run a runbook smoke (a compose-mode round trip exists since DB-6.1; this adds the runbook smoke and a host-mode run against Postgres 18) | Job green | – |
| DB-6.3 | Document WAL archiving and point-in-time recovery | Doc reviewed | – |
| DB-6.4 | Restore drill checklist with timings | Checklist filled once | – |

#### PR-DB-7 — Postgres HA

| Step | What | Done when | Needs |
|---|---|---|---|
| DB-7.1 | ADR: Patroni vs Postgres operator vs managed service (done: `docs/adr/0003-postgres-ha.md`, operator route) | ADR merged | – |
| DB-7.2 | Three-node lab deployment (done: the CI job `postgres-ha`, CloudNativePG 1.25.1 on kind) | `pg_isready` on the primary; two replicas streaming | DB-7.1, OPS-2.1 |
| DB-7.3 | Connection string with multiple hosts and `target_session_attrs=read-write` (done: `postgres.external.targetSessionAttrs` and a host list in the chart) | Services reconnect after a switchover | DB-7.2 |
| DB-7.4 | Failover test during the runbook replay (done with writes through SME and the e2e checks around the kill, not the full runbook replay) | Data intact; recovery time recorded | DB-7.3 |

### 5.3 Messaging and jobs (`PR-MSG`)

Webhooks go out best-effort and inline through `smo_shared/webhook.py` (0 retries for notifications, 3 for others per
`STANDARDS.md`). A restart or an unreachable subscriber loses events.

#### PR-MSG-1 — Transactional outbox (done except two inline leftovers)

Done (`HISTORY.md` §10): `smo_shared/outbox.py` (table, `enqueue`, `drain`, inline drain after commit), the call-site inventory `docs/NOTIFICATIONS.md`, and every class-A notification in every module goes through it. Left inline on purpose: the DME stop-job `DELETE` (an outbox row carries only a POST body: a method column would move it) and the two reads whose answer the caller needs.

| Step | What | Done when | Needs |
|---|---|---|---|

#### PR-MSG-2 — Delivery worker

| Step | What | Done when | Needs |
|---|---|---|---|
| MSG-2.1 | `ROLE=worker` entrypoint (same image) and a compose service (done as `smo_shared.worker`, MSG-4; it now also runs the outbox sweep) | Worker starts and idles | MSG-1.2 |
| MSG-2.2 ★ | Claim rows with `FOR UPDATE SKIP LOCKED` | Two workers never send the same row (test) | MSG-2.1 |
| MSG-2.3 | Back-off schedule 0 / 5 / 10 / 20 s as in `STANDARDS.md`, then `DEAD` | Fake-clock test | MSG-2.2 |
| MSG-2.4 | Admin routes: list by status, requeue a `DEAD` row | Route tests | MSG-2.3 |
| MSG-2.5 | Turn off inline `drain` when a worker is configured | Runbook replay green with worker only | MSG-2.3 |
| MSG-2.6 | Counters: sent, failed, dead, queue depth | Visible on `/metrics` | OBS-2.3 |

#### PR-MSG-3 — Event bus

| Step | What | Done when | Needs |
|---|---|---|---|
| MSG-3.1 | CloudEvents-shaped envelope schema (type, source, id, time, data) | Schema and examples in `docs/` | – |
| MSG-3.2 | `EventPublisher` interface in `smo_shared` | Unit test with an in-memory publisher | MSG-3.1 |
| MSG-3.3 | Postgres implementation (outbox rows plus `LISTEN/NOTIFY` wake-up) | Subscriber receives within a second | MSG-3.2, MSG-1.2 |
| MSG-3.4 | Kafka implementation behind an optional extra | Round trip against a Kafka container in a compose profile | MSG-3.2 |
| MSG-3.5 | Publish FSM state-change events from one hook in `smo_shared/statemachine.py` | Event seen for `rapp_instance` transitions | MSG-3.2 |
| MSG-3.6 | Publish alarm raised / cleared events | Event seen | MSG-3.2 |

#### PR-MSG-4 — Durable job runner (the periodic part is done: `HISTORY.md` PR-MSG-4, the worker; open: the generic `job` table and its queue-shaped users)

| Step | What | Done when | Needs |
|---|---|---|---|
| MSG-4.1 | Generic `job` table: type, payload, status, progress, result, cancel_requested, lease_until | Migration applied | – |
| MSG-4.2 | Worker claims and runs a registered handler | Handler runs once across two workers | MSG-4.1, MSG-2.1 |
| MSG-4.3 | Cancel flag checked between handler steps | Cancel stops a running job | MSG-4.2 |
| MSG-4.4 | Lease expiry: another worker resumes an abandoned job | Kill-the-worker test | MSG-4.2 |
| MSG-4.5 | First user: RAN NF OAM southbound config sub-changes | `ST-9.3` done; same API behaviour | MSG-4.2 |
| MSG-4.6 | Second user: software-management jobs | Same | MSG-4.2 |

#### PR-MSG-5 — Signing and delivery log

| Step | What | Done when | Needs |
|---|---|---|---|
| MSG-5.1 | Optional per-subscription secret field | Migration on one subscription model | – |
| MSG-5.2 | HMAC-SHA256 header over the body | Receiver-side example verifies | MSG-5.1 |
| MSG-5.3 | `GET` delivery history per subscription from the outbox | Route test | MSG-1.4 |

#### PR-MSG-6 — SSRF check at send time

| Step | What | Done when | Needs |
|---|---|---|---|
| MSG-6.1 | Resolve the hostname at send and apply the existing blocked-address rules to every result | Test with a hostname that resolves to loopback is refused | – |
| MSG-6.2 | Connect to the checked address (pinned transport), keep the original `Host` | Rebinding test cannot switch addresses | MSG-6.1 |
| MSG-6.3 | Flag so fictional hostnames in unit tests keep working | Existing tests green | MSG-6.1 |

### 5.4 Security (`PR-SEC`)

`SECURITY.md` says the build is not hardened. `/bootstrap` and `/health` are unauthenticated and service-to-service calls
are plain HTTP.

#### PR-SEC-1 — TLS at the edge

| Step | What | Done when | Needs |
|---|---|---|---|

#### PR-SEC-2 — mTLS between services

| Step | What | Done when | Needs |
|---|---|---|---|
| SEC-2.1 | Per-service certificates from the dev CA | Script output | – |
| SEC-2.2 | Uvicorn option to require client certs, by env | Call without a cert is refused | SEC-2.1 |
| SEC-2.3 | `R1Client` and every internal `httpx` call present a client cert and verify the CA | Runbook replay green | SEC-2.2 |
| SEC-2.4 | `sslmode=verify-full` for Postgres | Connection fails with a wrong CA | SEC-2.1 |
| SEC-2.5 | Rotation procedure (documented, tested once) | Rotation with no downtime | SEC-2.3 |

#### PR-SEC-3 — Service mesh option

| Step | What | Done when | Needs |
|---|---|---|---|
| SEC-3.1 | Doc: sidecar injection for Istio or Linkerd | Doc reviewed | OPS-2.1 |
| SEC-3.2 | Strict mTLS policy manifests | Plain-HTTP call between pods fails | SEC-3.1 |
| SEC-3.3 | Per-module caller allowlist (who may call whom) from `ARCHITECTURE.md` | Denied call proves the rule | SEC-3.2 |

#### PR-SEC-4 — Secret management

| Step | What | Done when | Needs |
|---|---|---|---|
| SEC-4.4 | Same for GUI admin password and session key | Same | – |
| SEC-4.5 | Same for the module invoker secret (`module_identity.invoker_secret`, or `SMO_INVOKER_SECRET`, which already overrides it) | Same | – |
| SEC-4.7 | External Secrets or Vault example manifest | Example applies on a lab cluster | OPS-2.3 |

#### PR-SEC-5 — Signing keys and token caching

SME access tokens are opaque and introspected (RFC 7662); the signed tokens are the GUI session JWTs (HS256 today).

| Step | What | Done when | Needs |
|---|---|---|---|
| SEC-5.1 | BFF option for RS256/ES256 with a key file | Login works with each algorithm | – |
| SEC-5.2 | `kid` header and a key set (current and previous) for rotation | Token signed with the old key still verifies | SEC-5.1 |
| SEC-5.3 | `/.well-known/jwks.json` on the BFF | Route test | SEC-5.1 |
| SEC-5.4 ★ | Short TTL cache of introspection results at R1 Termination, dropped on revocation | Load test shows fewer SME calls; revoked token rejected within the TTL | – |

#### PR-SEC-6 — OIDC login for the GUI

| Step | What | Done when | Needs |
|---|---|---|---|
| SEC-6.1 | Config: issuer, client id and secret, redirect URL, scopes | Startup validates config | – |
| SEC-6.2 | Authorization-code with PKCE routes (`/api/oidc/login`, `/callback`) | Works against a local Keycloak container | SEC-6.1 |
| SEC-6.3 | ID token validation via the issuer's JWKS | Bad signature and wrong audience refused | SEC-6.2 |
| SEC-6.4 | Group claim → `rbac.py` role mapping from config | Mapped user gets the right role | SEC-6.3 |
| SEC-6.5 | Create the user on first login | Row appears; no password stored | SEC-6.3 |
| SEC-6.6 | Logout and end-session redirect | Session cookie cleared | SEC-6.2 |
| SEC-6.7 | Local admin kept as break-glass behind a flag | Flag off disables local login | SEC-6.2 |
| SEC-6.8 | LDAP bind as an alternative provider (optional) | Login works against an OpenLDAP container | SEC-6.1 |

#### PR-SEC-7 — MFA and logout revocation

| Step | What | Done when | Needs |
|---|---|---|---|
| SEC-7.1 | `gui_user_totp` table and enrol route | QR secret generated and verified | – |
| SEC-7.2 | Login second step | Wrong code refused | SEC-7.1 |
| SEC-7.3 | Recovery codes (hashed) | One-time use | SEC-7.1 |
| SEC-7.4 | Server-side session row so logout revokes a session (today only `token_version` bumps do) | Token refused after logout | – |
| SEC-7.5 | Admin action: revoke a user's sessions | Route test | SEC-7.4 |

#### PR-SEC-8 — Rate and size limits

| Step | What | Done when | Needs |
|---|---|---|---|
| SEC-8.3 | Separate stricter limit for the unauthenticated paths | Test | – |
| SEC-8.4 | Limits per route class (read, write, upload) from config | Config test | – |
| SEC-8.5 | Shared limiter state (Postgres) so replicas share a budget | Two replicas share one bucket | – |
| SEC-8.6 | Same limiter on the BFF login route | Brute-force test | – |

#### PR-SEC-9 — Bootstrap exposure

| Step | What | Done when | Needs |
|---|---|---|---|
| SEC-9.1 | Document why `/bootstrap` is open and what it reveals | Paragraph in `r1-termination/README.md` | – |
| SEC-9.2 | NetworkPolicy / ingress rule limiting `/bootstrap` to rApp networks | Manifest and test | OPS-2.6 |
| SEC-9.3 | Optional shared bootstrap key header, off by default | Test both modes | – |

#### PR-SEC-10 — Tenant / region authorization

| Step | What | Done when | Needs |
|---|---|---|---|
| SEC-10.1 | ADR: where scope is enforced (R1 Termination vs each module) | ADR merged | – |
| SEC-10.2 | `region` and `tenant` columns on `managed_entity` | Migration; set by registration | – |
| SEC-10.3 | Scope claim on the invoker (registration field, returned by introspection) | Introspection returns it | – |
| SEC-10.4 ★ | Pilot: `POST /config-jobs` refuses a target outside the caller's scope | 403 test | SEC-10.2, SEC-10.3 |
| SEC-10.5 | Same on `GET .../config` | 403 test | SEC-10.4 |
| SEC-10.6 | Same on alarms and PM reads | 403 test | SEC-10.4 |
| SEC-10.7 | Same on rApp-facing DME and MLMR reads | 403 test | SEC-10.3 |
| SEC-10.8 | OPA sidecar as an alternative decision point (optional) | Same tests pass with it | SEC-10.1 |

#### PR-SEC-11 — Tamper-evident audit

| Step | What | Done when | Needs |
|---|---|---|---|
| SEC-11.1 | Audit table in `smo_shared`: actor, action, target, result, correlation id, prev_hash, hash | Migration | – |
| SEC-11.2 | `audit(...)` helper that chains hashes | Unit test detects an edited row | SEC-11.1 |
| SEC-11.3 ★ | R1 Termination audits every proxied mutating call | One row per POST/PUT/PATCH/DELETE | SEC-11.2 |
| SEC-11.4 | `verify_audit` command | Reports the first broken link | SEC-11.2 |
| SEC-11.5 | Export as JSON lines and syslog | Output sample | SEC-11.2 |
| SEC-11.6 | BFF audit view merges platform audit | GUI test | SEC-11.3 |

#### PR-SEC-12 — Supply-chain evidence

| Step | What | Done when | Needs |
|---|---|---|---|
| SEC-12.1 | SBOM generation per image in CI | Artifact attached | – |
| SEC-12.2 | Image vulnerability scan with a severity gate (built in `image-scan.yml`; not yet shown to fail on a seeded finding in CI: `tests_integration/test_scan_summary.py` shows the gate on Trivy-shaped results) | Gate fails on a seeded finding | – |

#### PR-SEC-13 — Container hardening

| Step | What | Done when | Needs |
|---|---|---|---|
| SEC-13.4 | Same settings in the Helm chart | `kubectl` shows them | OPS-2.2 |

#### PR-SEC-14 — Threat model

| Step | What | Done when | Needs |
|---|---|---|---|
| SEC-14.1 | Data-flow diagram of R1, O1, A1, O2, GUI, DB | Diagram in `docs/` | – |
| SEC-14.2 | STRIDE table per flow | Table with a mitigation or an item ID per row | SEC-14.1 |
| SEC-14.3 | Findings imported as items in this file | Each has an ID | SEC-14.2 |
| SEC-14.4 | Scope for an external penetration test | One-page scope | SEC-4.5 |


### 5.5 Observability (`PR-OBS`)

HTTP request metrics and `/metrics` exist (`PR-OBS-2`, `HISTORY.md` §10); no OpenTelemetry usage exists in the code (checked). A correlation id exists in
`smo_shared/correlation.py`. Liveness and readiness are `PR-ST-7`.

#### PR-OBS-2 — Metrics (open: OBS-2.8; `HISTORY.md` §10 for 2.4–2.6)

| Step | What | Done when | Needs |
|---|---|---|---|
| OBS-2.8 | Committed Grafana dashboard JSON for the golden signals | Imports cleanly | OBS-2.3 |

#### PR-OBS-3 — Distributed traces

| Step | What | Done when | Needs |
|---|---|---|---|
| OBS-3.1 | Parse and propagate `traceparent` next to the correlation id (`correlation.py`) | Header survives R1 hop (test) | – |
| OBS-3.2 | R1 Termination forwards it | Integration test | OBS-3.1 |
| OBS-3.3 | OpenTelemetry SDK, off unless `OTEL_EXPORTER_OTLP_ENDPOINT` is set | No overhead when off | OBS-3.1 |
| OBS-3.4 | FastAPI, httpx and SQLAlchemy instrumentation | A request shows 3 span kinds | OBS-3.3 |
| OBS-3.5 | Collector plus Jaeger or Tempo in a compose profile | Trace visible for a runbook call | OBS-3.4 |

#### PR-OBS-4 — Business metrics

| Step | What | Done when | Needs |
|---|---|---|---|
| OBS-4.1 | Gauges: packages, rApp instances and NF deployments by state | Values match the DB | OBS-2.2 |
| OBS-4.2 | Alarms by severity and ack state | Same | OBS-2.2 |
| OBS-4.3 | O1 write outcome and retry counters | Counters move in an O1 test | OBS-2.2 |
| OBS-4.4 | Model and runtime lifecycle counts | Same | OBS-2.2 |
| OBS-4.5 | Pending approvals and their age | Same | OBS-2.2 |

#### PR-OBS-5 — Alerts and SLOs

| Step | What | Done when | Needs |
|---|---|---|---|
| OBS-5.1 | SLI definitions (availability, latency, correctness) in `docs/` | Doc reviewed | – |
| OBS-5.2 | Availability and error-rate alert rules | `promtool check rules` green | OBS-2.2 |
| OBS-5.3 | Latency alert rules | Same | OBS-2.2 |
| OBS-5.4 | O1 write failure rate rule | Same | OBS-4.3 |
| OBS-5.5 | Outbox DEAD-row depth rule | Same | MSG-2.6 |
| OBS-5.6 | Each rule links to its runbook entry | Link check | OBS-7.1 |

#### PR-OBS-6 — Log shipping

| Step | What | Done when | Needs |
|---|---|---|---|
| OBS-6.1 | Fluent Bit config reading container JSON logs | Logs forwarded | – |
| OBS-6.2 | Loki and Grafana in a compose profile | Search by correlation id works | OBS-6.1 |
| OBS-6.3 | Elasticsearch field mapping doc | Doc reviewed | – |

#### PR-OBS-7 — Runbooks

| Step | What | Done when | Needs |
|---|---|---|---|
| OBS-7.1 | Runbook template and index | Template merged | – |
| OBS-7.2 | Entries: Postgres down, SME down, R1 down | Each tried once on the compose stack | – |
| OBS-7.3 | Entries: O1 write failures, adaptor unreachable | Same | – |
| OBS-7.4 | Entries: webhook backlog, DEAD rows | Same | MSG-2.4 |
| OBS-7.5 | Entry: certificate expiry and rotation | Same | SEC-2.5 |
| OBS-7.6 | Entry: backup and restore | Same | DB-6.4 |

#### PR-OBS-8 — Self-monitoring

| Step | What | Done when | Needs |
|---|---|---|---|
| OBS-8.1 | `/version` per module (build SHA from an env set in the image) | Route test | – |
| OBS-8.2 | BFF `GET /modules/status` adds readiness and version | Test | OBS-8.1 |
| OBS-8.3 | GUI shows readiness and version columns | Component test | OBS-8.2 |

### 5.6 Packaging, migrations and release (`PR-OPS`)

#### PR-OPS-1 — Real migrations (all steps done: `HISTORY.md` §10)

Alembic is in place and compose runs it (`docs/adr/0001-schema-migrations.md`, `HISTORY.md` §10): baseline `0001` is `001_init.sql`, revision `0002` is the notification outbox, `scripts/migrate.py` upgrades, stamps or downgrades, and the `migrate` service runs before the modules.

| Step | What | Done when | Needs |
|---|---|---|---|

#### PR-OPS-2 — Helm chart

| Step | What | Done when | Needs |
|---|---|---|---|
| OPS-2.1 ★ | Chart skeleton and `values.yaml` | `helm lint` green | – |
| OPS-2.2 | One generic template looped over modules; `onboarding` first | Pod runs on kind | OPS-2.1 |
| OPS-2.3 | Config and secret wiring (env, `*_FILE`) | Pod reads DB URL from a Secret | OPS-2.2 |
| OPS-2.4 | Probes from `/live` and `/ready` | Probes pass | OPS-2.2 |
| OPS-2.5 | Services, plus Ingress for R1 Termination and the GUI | Reachable from the kind host | OPS-2.2 |
| OPS-2.6 | NetworkPolicy equal to the compose network rules (`a1_mock_net` isolation included) | Denied-path test | OPS-2.2 |
| OPS-2.7 | PodDisruptionBudget and HPA templates (off by default) | `helm template` renders | OPS-2.2 |
| OPS-2.8 | CI: `helm lint` and a kind install | Job green | OPS-2.5 |
| OPS-2.9 | Runbook replay against the kind install | Replay green | OPS-2.8 |

#### PR-OPS-3 — Migration as a release hook

| Step | What | Done when | Needs |
|---|---|---|---|
| OPS-3.1 | Pre-upgrade Helm hook Job runs the migrations once | Upgrade runs it once | OPS-1.5, OPS-2.2 |
| OPS-3.2 | Services refuse to become ready on an older schema | Test | ST-7.4 |

#### PR-OPS-4 — Releases (open: 4.1b, 4.2 onward)

Tag scheme and `CHANGELOG.md` exist (`PR-OPS-4.1`, `HISTORY.md` §10); no tag has been cut.

| Step | What | Done when | Needs |
|---|---|---|---|

#### PR-OPS-5 — Rolling upgrade

| Step | What | Done when | Needs |
|---|---|---|---|
| OPS-5.3 | Mixed-version run (two versions side by side) through the replay | Replay green | OPS-5.2, HA-1.1 |

#### PR-OPS-6 — GitOps example

| Step | What | Done when | Needs |
|---|---|---|---|
| OPS-6.1 | Kustomize overlays: lab, staging, prod | `kustomize build` green | OPS-2.2 |
| OPS-6.2 | Argo CD `Application` example | Syncs on a lab cluster | OPS-6.1 |

#### PR-OPS-7 — Configuration reference

| Step | What | Done when | Needs |
|---|---|---|---|
| OPS-7.1 | Script that lists every `os.environ` read per module | Output file | – |
| OPS-7.2 | Table: name, default, secret or not, owner | In `docs/` | OPS-7.1 |
| OPS-7.3 | CI check: a new env read must appear in the table | Fails on a seeded miss | OPS-7.2 |

#### PR-OPS-8 — Feature flags

| Step | What | Done when | Needs |
|---|---|---|---|
| OPS-8.1 | `flag("NAME")` helper (env-backed, default off) | Unit test | – |
| OPS-8.2 | Convention: incomplete production items ship behind a flag | Rule in `CLAUDE.md` | OPS-8.1 |

#### PR-OPS-9 — Sizing

| Step | What | Done when | Needs |
|---|---|---|---|
| OPS-9.1 | Default CPU and memory requests/limits in `values.yaml` | Pods schedule on kind | OPS-2.2 |
| OPS-9.2 | Replace guesses by measured values | Table with the load that produced each | QA-1.4 |

#### PR-OPS-10 — Development-sanity pipeline on GitHub Actions (Target 1) (OPS-10.1 to 10.5 done: `HISTORY.md` §10)

Purpose: tell the team, on every merge, that the whole stack still comes up and works. Runs on GitHub Actions only (free minutes; no GUI to open, headless checks only). It starts as option (a), *tear down and redeploy the whole stack on every merge to master*, and grows into option (b), *packaging and proper upgrades*, as the `PR-OPS` features below land. Option (a) always starts from empty data, so it cannot catch upgrade bugs; that is what the (b) steps add.

| Step | What | Done when | Needs |
|---|---|---|---|
| OPS-10.6 | (b) Helm on kind replaces the compose lane as the master gate; compose stays for local use | Master gate runs `OPS-2.8` and `OPS-2.9` | OPS-2.9, OPS-10.5 |
| OPS-10.7 | (b) Rolling-upgrade lane (mixed versions) added to the master gate | Replay green | OPS-5.3, OPS-10.6 |

Decision rule: `OPS-10.5` is in place; the master gate stays option (a) until `OPS-10.6` (Helm on kind) is done. Do not remove the compose lane before `OPS-10.6` has been green for a few merges.

#### PR-OPS-11 — Demo environment on GitHub Codespaces (Target 2)

Purpose: show the GUI and the rApp flows to people. Used **on demand only**: create a fresh codespace for the demo, use it, then delete it. It is never the development gate (that is `PR-OPS-10`), and it is not a permanent deployment. The reason to scrap it after each demo is to save the free core-hours.

| Step | What | Done when | Needs |
|---|---|---|---|
| OPS-11.1 ★ | Set the Codespaces spending limit to **$0** on the owner account (GitHub Settings, Billing, Spending limits) as a safeguard; a person does this | Limit reads $0 | – |
| OPS-11.2 | `.devcontainer/devcontainer.json` sized for the stack (4 cores, Docker-in-Docker), forwarding the GUI and R1 ports | Fresh codespace opens with ports listed | – |
| OPS-11.3 | `scripts/codespace-up.sh`: create secrets, `docker compose up`, wait for health, seed the sample rApp, print the GUI URL | One command from a fresh codespace to a working GUI | OPS-11.2 |
| OPS-11.4 | Demo procedure in `DEMO_RUNBOOK.md`: create, run, delete after the demo, with the idle timeout set short (30 minutes) | Procedure reviewed and followed once | OPS-11.3 |
| OPS-11.5 | Optional prebuild of the image on master so a fresh codespace starts quickly (watch prebuild storage; skip if it costs more than it saves) | Start time measured before and after | OPS-11.2 |
| OPS-11.6 | Once Helm exists: the same script installs the chart on kind so the demo shows the packaged install (check it fits the machine) | Demo runs on the chart | OPS-2.9, OPS-11.3 |

### 5.7 High availability and DR (`PR-HA`)

Later by design; each feature assumes the stateless, database and messaging steps it names.

#### PR-HA-1 — Run replicas

| Step | What | Done when | Needs |
|---|---|---|---|
| HA-1.1 | Two replicas per module in compose (`deploy.replicas`) or Helm (done in Helm: `ci/ha-values.yaml`, CI job `helm`; Onboarding, GUI backend and the mocks stay at one) | All start | – |
| HA-1.2 | Replay the runbook against the replicas (CI job `compose-replicas`: `docker-compose.replicas.yml`, two of each module, callers reach them through Docker's DNS) | Green | HA-1.1 |
| HA-1.3 | Fix list from failures in HA-1.2, one PR each (done: the replay passed on its first run, so the list is empty; spread of calls over replicas is checked on kind, see `CHANGELOG.md`) | List empty | HA-1.2 |

#### PR-HA-2 — Rolling restart

| Step | What | Done when | Needs |
|---|---|---|---|
| HA-2.1 | Restart one replica at a time during a replay (done with a health probe, not yet the runbook: `scripts/k8s_rolling_probe.py`) | No failed calls beyond retries | HA-1.2 |
| HA-2.2 | Same for the gateway | Same | HA-2.1 |

#### PR-HA-3 — Database failover

| Step | What | Done when | Needs |
|---|---|---|---|
| HA-3.1 | Switchover during a replay (not done: only the unplanned kill is exercised) | Recovery time recorded | DB-7.4 |
| HA-3.2 | Primary kill (unplanned) during a replay (done as DB-7.4: a marker row committed before the kill is on the new primary) | No data loss for committed work | DB-7.4 |

#### PR-HA-4 — Worker failover

| Step | What | Done when | Needs |
|---|---|---|---|
| HA-4.1 | Kill the delivery worker mid-batch (done: the sweep of `MSG-2` was missing and is built here; CI job `helm`) | No lost notification; duplicates only where at-least-once allows | MSG-2.2 |
| HA-4.2 | Kill the job runner mid-job (done on kind: the worker is scaled to 0 between the waves of a staged job, the job waits, and finishes when the worker is back; CI job `helm`) | Job resumes (`MSG-4.4`) | MSG-4.4 |

#### PR-HA-5 — Placement

| Step | What | Done when | Needs |
|---|---|---|---|
| HA-5.1 | Anti-affinity and topology spread in the chart (done as topology spread; rendering checked in CI, placement on several nodes not yet: the CI cluster has one node) | Pods land on different nodes | OPS-2.7 |

#### PR-HA-6 — Disaster recovery

| Step | What | Done when | Needs |
|---|---|---|---|
| HA-6.1 | RPO and RTO targets written down | Numbers agreed | – |
| HA-6.2 | Off-site backup shipping | Restore from the off-site copy | – |
| HA-6.3 | Restore order and re-pointing steps (GUI, R1, adaptors) | One full drill with timings | HA-6.2 |

#### PR-HA-7 — Geo-redundancy

| Step | What | Done when | Needs |
|---|---|---|---|
| HA-7.1 | ADR: active/standby design | ADR merged | HA-6.3 |
| HA-7.2 | Cross-site replication | Standby lags by less than the RPO | HA-7.1, DB-7.2 |
| HA-7.3 | Controlled failover and failback drill | Drill report | HA-7.2 |


### 5.8 Southbound realism (`PR-SB`)

Both southbound ends are mocks (`mock-o1-adaptor`, `mock-near-rt-ric`). The NETCONF path sends an RFC 6241-shaped
`<edit-config>` as XML over plain HTTP to `O1AdaptorEndpoint.adaptor_uri` (`ran-nf-oam/app/netconf_client.py`).
FOCOM and NFO are model-level.

#### O1

#### PR-SB-1 — NETCONF over SSH (SB-1.1–1.9 done: `HISTORY.md` §10; open: the job-level candidate transaction and the compose replay over SSH, below)

| Step | What | Done when | Needs |
|---|---|---|---|

#### PR-SB-2 — Adaptor credentials and trust (SB-2.1–2.5 done: `HISTORY.md` §10)

| Step | What | Done when | Needs |
|---|---|---|---|


#### PR-SB-4 — WG4 O-RU YANG

| Step | What | Done when | Needs |
|---|---|---|---|
| SB-4.1 | Add the WG4 M-plane modules to `specs/` | Files merged | **The files themselves: they come from the O-RAN Alliance under its own licence. The public YangModels mirror does not carry them (checked), and they are not written from memory.** |
| SB-4.2 | Run the ingest script on them (`scripts/ingest_yang_schema.py` already takes any directory, with the 3GPP library: no code change expected) and commit the descriptor | Descriptors generated | SB-4.1 |
| SB-4.3 | Register the O-RU managed-function classes in the vendor capability registry | Registry test | SB-4.2 |
| SB-4.4 | Tests for one O-RU write | Test green | SB-4.3 |

#### PR-SB-5 — YANG-validated writes (open: 5.3 onward)

A CM write's values are checked against the leaf's YANG type, range, length, pattern, fraction digits and enum before dispatch (`app/leafcheck.py`, `HISTORY.md` §10).

| Step | What | Done when | Needs |
|---|---|---|---|
| SB-5.3 | Map failures to `rejection_reason` codes | Test | SB-5.2 |
| SB-5.4 | Unknown-attribute policy flag: reject or pass | Both modes tested | SB-5.2 |
| SB-5.5 | `must` constraint support (enum, pattern, length and range are done: `SB-5.1`) | Tests | SB-5.2 done |

#### PR-SB-6 — MO containment tree (`SA-RANOAM-4`; SB-6 done: `HISTORY.md` §10)

| Step | What | Done when | Needs |
|---|---|---|---|

#### PR-SB-7 — VES event receiver

| Step | What | Done when | Needs |
|---|---|---|---|
| SB-7.1 | `POST /ves/eventListener/v7` accepting the batch and single-event schemas | Schema test | – |
| SB-7.2 ★ | Map the fault domain to the existing `/alarms/ingest` path | Alarm row created | SB-7.1 |
| SB-7.3 | Map `heartbeat` to the adaptor heartbeat | Health updated | SB-7.1 |
| SB-7.4 | Map `measurement` and `stndDefined` PM to the `/pm-reports` path | PM record created | SB-7.1 |
| SB-7.5 | Basic auth for the listener, credentials through the `*_FILE` helper (`smo_shared/secretfile.py`) | 401 without it | SB-7.1 |
| SB-7.6 | Kafka consumer variant | Same events via a topic | SB-7.1, MSG-3.4 |

#### PR-SB-8 — Streaming PM (`SA-RANOAM-8`)

| Step | What | Done when | Needs |
|---|---|---|---|
| SB-8.1 | ADR: transport (Kafka, gRPC or chunked HTTP) | ADR merged | – |
| SB-8.2 | `delivery_method=stream` subscription resolves to a topic or endpoint | Route test | SB-8.1 |
| SB-8.3 | Producer side from the VES and PM ingest paths | Messages on the topic | SB-8.2, SB-7.4 |
| SB-8.4 | MDAF `STREAMING` subscription consumes it (closes `SA-MDA-5`'s recorded-only gap) | End-to-end test | SB-8.3 |
| SB-8.5 | Backpressure and drop policy | Slow consumer test | SB-8.3 |

#### PR-SB-9 — Vendor adaptor conformance kit

| Step | What | Done when | Needs |
|---|---|---|---|
| SB-9.1 | List of checks per service: CM, FM, PM, SW, discovery, heartbeat | Doc | – |
| SB-9.2 | CM checks as a test pack | Pack passes against the mock | SB-9.1 |
| SB-9.3 | FM checks | Same | SB-9.1 |
| SB-9.4 | PM checks | Same | SB-9.1 |
| SB-9.5 | SW and discovery checks | Same | SB-9.1 |
| SB-9.6 | CLI runner and a report file | Report generated | SB-9.2 |
| SB-9.7 | CI runs it against the mock adaptor | Job green | SB-9.6 |

#### PR-SB-10 — First vendor profile

Needs access to a vendor simulator or lab.

| Step | What | Done when | Needs |
|---|---|---|---|
| SB-10.1 | Collect the vendor's YANG set | Files in a profile directory | – |
| SB-10.2 | Add a capability registry entry | Registry test | SB-10.1 |
| SB-10.3 | List deviations from the standard models | List in the profile README | SB-10.1 |
| SB-10.4 | Conformance pack run against the vendor | Report attached | SB-9.6, SB-1.9 |

#### A1

#### PR-SB-11 — RIC inventory (`OI-5-a1-ric-inventory`)

| Step | What | Done when | Needs |
|---|---|---|---|
| SB-11.1 | `near_rt_ric` table: id, URL, auth ref, status | Migration | – |
| SB-11.2 | `POST` and `GET /rics` | Route tests | SB-11.1 |
| SB-11.3 | Health probe per RIC | Status changes when the mock stops | SB-11.2 |
| SB-11.4 | Fetch policy types from `GET /policytypes` on each RIC | Types and schemas stored | SB-11.2 |
| SB-11.5 | Map each policy to its RIC | A policy to RIC B goes to B | SB-11.4 |
| SB-11.6 | Remove the hardcoded `KNOWN_POLICY_TYPES` fallback behind a flag | Test with the flag on | SB-11.4 |

#### PR-SB-12 — A1 OWN/OTHERS scope (`OI-5-a1-scope`)

| Step | What | Done when | Needs |
|---|---|---|---|
| SB-12.1 | Record the subscriber's rApp id from the token on subscription | Column filled | – |
| SB-12.2 | Compare with `creator_id` for OWN and OTHERS | Test for each scope | SB-12.1 |

#### PR-SB-13 — RIC simulator lane

| Step | What | Done when | Needs |
|---|---|---|---|
| SB-13.1 | Compose profile with the O-RAN-SC `near-rt-ric-simulator` | Starts | – |
| SB-13.2 | Policy create, read, delete against it | Test green | SB-13.1, SB-11.2 |
| SB-13.3 | Nightly CI job | Job green | SB-13.2 |

#### O2

#### PR-SB-14 — O2-IMS client

| Step | What | Done when | Needs |
|---|---|---|---|
| SB-14.1 | ADR: target (an O2-IMS simulator or a real IMS) and auth | ADR merged | – |
| SB-14.2 | HTTP client with auth and timeouts | Unit tests with a stub server | SB-14.1 |
| SB-14.3 | Pull resource pools | Pools appear in FOCOM | SB-14.2 |
| SB-14.4 | Pull resources and resource types | Same | SB-14.3 |
| SB-14.5 | Reconcile: add, update, remove | Deleted upstream means removed | SB-14.4 |
| SB-14.6 | Inventory change subscription to the IMS | Event updates FOCOM | SB-14.5 |

#### PR-SB-15 — Async provisioning (`SA-FOCOM-7`)

| Step | What | Done when | Needs |
|---|---|---|---|
| SB-15.1 | Driver interface for `ProvisioningRequest` fulfilment; today's model-level fulfilment becomes the default driver | Existing tests green | – |
| SB-15.2 | Phase fields persisted: `PENDING`, `PROGRESSING`, `FAILED` | Migration; states visible | SB-15.1 |
| SB-15.3 | Fulfilment runs as a job | Request returns before completion | SB-15.2, MSG-4.2 |
| SB-15.4 | Failure and timeout handling | `FAILED` with a reason | SB-15.3 |
| SB-15.5 | Cancel | Test | SB-15.3, MSG-4.3 |

#### PR-SB-16 — Kubernetes driver for NFO

| Step | What | Done when | Needs |
|---|---|---|---|
| SB-16.1 | Driver interface in NFO; today's behaviour is the default driver | Existing tests green | – |
| SB-16.2 | K8s client; instantiate creates a Deployment from the descriptor | Pod runs on kind | SB-16.1 |
| SB-16.3 | Status watch drives the `NFDeployment` FSM | State follows pod readiness | SB-16.2 |
| SB-16.4 | Heal (rollout restart) | Pod replaced | SB-16.3 |
| SB-16.5 | Terminate | Resources removed | SB-16.3 |
| SB-16.6 | RBAC manifest for the NFO service account | Least-privilege role documented | SB-16.2 |
| SB-16.7 | kind-based CI test | Job green | SB-16.5 |

#### PR-SB-17 — Scale target size (`OI-7`)

| Step | What | Done when | Needs |
|---|---|---|---|
| SB-17.1 ★ | `replicas` argument on `POST /nfo/deployments/{id}/scale` | Route test | – |
| SB-17.2 | `resources` argument | Route test | SB-17.1 |
| SB-17.3 | Validate against the manifest runtime-profile bounds | Out-of-bounds refused | SB-17.1 |
| SB-17.4 | AIMgF `runtime/scale` request carries the size | End-to-end test | SB-17.3 |
| SB-17.5 | K8s driver applies it | Replica count changes on kind | SB-17.4, SB-16.3 |

#### PR-SB-18 — FOCOM PM collector (`SA-FOCOM-6`)

| Step | What | Done when | Needs |
|---|---|---|---|
| SB-18.1 | `reportInterval` and `heartbeatInterval` stored and validated | Route tests | – |
| SB-18.2 | Scheduled collection task | One collection per interval across replicas | – |
| SB-18.3 | `PerformanceMeasurementStore` retention | Old rows purged | DB-3.2 |
| SB-18.4 | `FILE` reporting mode | File written and listed | SB-18.2 |
| SB-18.5 | `STREAM` reporting mode | Messages on a topic | SB-18.2, SB-8.1 |

### 5.9 Management function depth (`PR-MGT`)

Current state, checked: config writes run as `write_config_job` with `write_config_sub_change` rows and an MSAC check;
`GET .../config` reads the cache; software-management jobs and `/o1-adaptor-endpoints/discover` exist; alarms have
ack and clear routes.

#### Configuration management

#### PR-MGT-2 — MSAC beyond writes (`SA-RANOAM-1` reach)

| Step | What | Done when | Needs |
|---|---|---|---|
| MGT-2.1 | `authorize(..., "read")` on `GET .../config` | Denied read is 403 | – |
| MGT-2.2 | Same on PM and FM subscription create | Test | – |
| MGT-2.3 | Same on alarm ack and clear | Test | – |
| MGT-2.4 | Same on software-management jobs | Test | – |
| MGT-2.5 | Same on file routes | Test | – |

#### PR-MGT-3 — Dry run (done: `HISTORY.md` §10)

No steps open.

| Step | What | Done when | Needs |
|---|---|---|---|

#### PR-MGT-4 — Change windows and approvals

| Step | What | Done when | Needs |
|---|---|---|---|
| MGT-4.1 | `scheduled_at` and `window_end` on the job | Migration | – |
| MGT-4.2 | `PENDING_APPROVAL` state in the FSM | Transition tests | – |
| MGT-4.3 | Approve and reject routes; approver must differ from requester | Same-user approval refused | MGT-4.2 |
| MGT-4.4 | Start at the window | Job starts once across replicas | MGT-4.1, MSG-4.2 |
| MGT-4.5 | Expire after `window_end` | Job moves to `EXPIRED` | MGT-4.4 |

#### PR-MGT-6 — Drift detection

| Step | What | Done when | Needs |
|---|---|---|---|
| MGT-6.1 | Desired-state store per element | Migration | – |
| MGT-6.2 | On-demand compare with the actual config | Route test | MGT-6.1 |
| MGT-6.3 | Drift report with a count per element | Route test | MGT-6.2 |
| MGT-6.4 | Scheduled compare | One run per interval | MGT-6.2 |
| MGT-6.5 | Remediation as a config job | Test | MGT-6.2 |

#### PR-MGT-7 — Plan management (TS 28.572)

| Step | What | Done when | Needs |
|---|---|---|---|
| MGT-7.1 | Plan object model from `TS28572_PlanManagement.yaml` | Migration | – |
| MGT-7.2 | Create, read, delete routes | Route tests | MGT-7.1 |
| MGT-7.3 | Activation creates a config job | Values written | MGT-7.2 |
| MGT-7.4 | Conformance test against the spec file | Test green | MGT-7.2 |

#### Fault management

#### PR-MGT-8 — Alarm lifecycle depth

Ack and clear exist (`PATCH /alarms/{id}/ack`, `/clear`); an unknown alarm is a 404 and `new_state` must be `ACKNOWLEDGED` or `UNACKNOWLEDGED` (`MGT-8.1`, `HISTORY.md` §10).

| Step | What | Done when | Needs |
|---|---|---|---|
| MGT-8.2 | `alarm_history` table: every ack, clear and severity change | Migration; row per change | – |
| MGT-8.3 | Comments: add and list | Route tests | – |
| MGT-8.4 | List filters: severity, state, time range, element | Route tests | – |
| MGT-8.5 | Repeat raise of the same `source_alarm_id`: update count and time instead of a new row (confirm today's behaviour first) **(verify)** | Test | – |
| MGT-8.6 | Aging policy: auto-clear after N hours without a repeat | One run per interval | – |
| MGT-8.7 | Suppression windows per element (planned work) | Alarm in a window is flagged | MGT-8.2 |
| MGT-8.8 | FM subscription notifications for ack and clear (confirm what is sent today) **(verify)** | Receiver gets them | MSG-1.4 |

#### PR-MGT-9 — Correlation v1 (`OI-1-alarm-storm`)

| Step | What | Done when | Needs |
|---|---|---|---|
| MGT-9.1 | Key function: element, probable cause, time window | Unit tests | – |
| MGT-9.2 | Apply on ingest to set `correlation_group` | Two matching alarms share a group | MGT-9.1 |
| MGT-9.3 | Add `neighbourRefs` grouping | Neighbouring elements group | MGT-9.2 |
| MGT-9.4 | `root_cause_indicator` heuristic: earliest alarm in the group | Flag set on one alarm | MGT-9.2 |
| MGT-9.5 | `GET /alarm-groups` | Route test | MGT-9.2 |
| MGT-9.6 | Window and thresholds from env | Config test | MGT-9.2 |
| MGT-9.7 | Evaluate on recorded alarm traces (a replay script) | Precision and recall numbers recorded | MGT-9.4 |

#### PR-MGT-10 — Topology-aware root cause

| Step | What | Done when | Needs |
|---|---|---|---|
| MGT-10.1 | Parent-child suppression using the containment tree | Child alarms point at the parent's alarm | SB-6.4, MGT-9.2 |
| MGT-10.3 | Candidate scoring and the evaluation script from MGT-9.7 | Improvement shown on the traces | MGT-10.1 |

#### Performance management

#### PR-MGT-11 — KPI engine

| Step | What | Done when | Needs |
|---|---|---|---|

#### PR-MGT-12 — PM collection at scale

| Step | What | Done when | Needs |
|---|---|---|---|
| MGT-12.1 | Scheduled file fetch per adaptor | One fetch per interval across replicas | – |
| MGT-12.2 | Parser for the 3GPP XML PM file format | Parses sample files | – |
| MGT-12.3 | De-duplicate by file id | Test | MGT-12.1 |
| MGT-12.4 | Backlog gauge | Visible on `/metrics` | OBS-2.2 |
| MGT-12.5 | Bounded parallel fetch | Test | MGT-12.1 |

#### PR-MGT-13 — Trace and QoE

| Step | What | Done when | Needs |
|---|---|---|---|
| MGT-13.1 | Trace job model from `TS28623_TraceControlNrm.yaml` | Migration | – |
| MGT-13.2 | Create, read, delete routes | Route tests | MGT-13.1 |
| MGT-13.3 | Push the job to the adaptor | Mock receives it | MGT-13.2 |
| MGT-13.4 | Collect the trace file | File listed | MGT-13.3 |
| MGT-13.5 | QoE measurement collection model from `TS28623_QoEMeasurementCollectionNrm.yaml` | Migration and routes | MGT-13.1 |

#### Network lifecycle

#### PR-MGT-14 — Zero-touch onboarding

| Step | What | Done when | Needs |
|---|---|---|---|
| MGT-14.1 | Onboarding template store (initial config per element type) | Route tests | – |
| MGT-14.2 | Discovery (exists) triggers template selection | Test | MGT-14.1 |
| MGT-14.3 | Apply the template as a config job | Config applied | MGT-14.2 |
| MGT-14.4 | Software baseline check | Mismatch flagged | MGT-14.2 |
| MGT-14.5 | Onboarding status FSM | Transition tests | MGT-14.3 |

#### PR-MGT-15 — Software campaigns

| Step | What | Done when | Needs |
|---|---|---|---|
| MGT-15.1 | Campaign object over many software-management jobs | Migration | – |
| MGT-15.2 | Waves with a health gate | Gate failure halts | MGT-15.1 (the wave machinery is `MGT-5`, done) |
| MGT-15.3 | Campaign rollback | Test | MGT-15.1 |
| MGT-15.4 | Campaign report | Route test | MGT-15.1 |

#### PR-MGT-16 — Intent and rApp conflict handling

| Step | What | Done when | Needs |
|---|---|---|---|
| MGT-16.1 | Target-overlap detection between two intents | Unit tests | – |
| MGT-16.2 | `priority` field on intents | Migration | – |
| MGT-16.3 | Conflict record and notification | Test | MGT-16.1 |
| MGT-16.4 | Arbitration rule (higher priority wins; tie goes to the operator) | Test | MGT-16.2, MGT-16.3 |
| MGT-16.5 | Same check for two rApps writing one target through config jobs | Test | MGT-16.1 |
| MGT-16.6 | GUI list of open conflicts | Component test | MGT-16.3 |

#### PR-MGT-17 — SO SMOS saga semantics

| Step | What | Done when | Needs |
|---|---|---|---|
| MGT-17.1 | Compensation action field in the dispatch table | Schema test | – |
| MGT-17.2 | Record executed steps per order | Migration | – |
| MGT-17.3 | Run compensations in reverse on failure | Test | MGT-17.1, MGT-17.2 |
| MGT-17.4 | Resume from the failed step | Test | MGT-17.2 |

#### PR-MGT-18 — SA SMOS SLA assurance

| Step | What | Done when | Needs |
|---|---|---|---|
| MGT-18.1 | SLA objects (KPI, threshold, window) | Migration | – |
| MGT-18.2 | Monitor evaluates SLAs from the KPI engine | Breach detected | MGT-18.1, MGT-11.5 |
| MGT-18.3 | Breach events | Event delivered | MGT-18.2, MSG-1.4 |
| MGT-18.4 | Escalation steps with timers | Test | MGT-18.3 |


### 5.10 Northbound and OSS/BSS (`PR-NB`)

#### PR-NB-1 — Alarm forwarding to the NOC

FM subscriptions with callbacks exist; the steps below add destinations a NOC uses.

| Step | What | Done when | Needs |
|---|---|---|---|
| NB-1.1 | Destination model: type, address, filter | Migration | – |
| NB-1.2 | REST destination through the outbox | Receiver gets a new alarm | NB-1.1, MSG-1.4 |
| NB-1.3 | Kafka destination | Message on a topic | NB-1.1, MSG-3.4 |
| NB-1.4 | SNMP v2c trap | Trap seen by a test listener | NB-1.1 |
| NB-1.5 | SNMP v3 | Same with auth and privacy | NB-1.4 |
| NB-1.6 | Syslog destination | Message seen | NB-1.1 |
| NB-1.7 | Filters: severity, element, region | Test | NB-1.1 |

#### PR-NB-2 — Inventory and topology export

| Step | What | Done when | Needs |
|---|---|---|---|
| NB-2.1 | Versioned export schema (JSON Schema in `docs/`) | Schema merged | – |
| NB-2.2 | `GET /inventory/export`, paged | Route test | NB-2.1 |
| NB-2.3 | Delta export since a cursor | Test | NB-2.2 |
| NB-2.4 | Sample CMDB sync script | Script runs against the demo data | NB-2.3 |

#### PR-NB-3 — TS 28.532 MnS facade

| Step | What | Done when | Needs |
|---|---|---|---|
| NB-3.1 | ProvMnS read (`GET` MOI) over the registry and cache | Conformance test vs the spec file | – |
| NB-3.2 | ProvMnS `PATCH` → config job | Values written | NB-3.1 |
| NB-3.3 | FaultSupervision facade | Conformance test | – |
| NB-3.4 | PerfMnS facade | Conformance test | – |
| NB-3.5 | Facade auth and scope | 403 test | NB-3.1, SEC-10.4 |

#### PR-NB-4 — Network slice management objects

| Step | What | Done when | Needs |
|---|---|---|---|
| NB-4.1 | Choose the object subset from TS 28.541 and 28.531 in `specs/` | One-page ADR | – |
| NB-4.2 | Slice profile model | Migration | NB-4.1 |
| NB-4.3 | Allocate, modify, deallocate as service orders | Test via SO SMOS | NB-4.2 |
| NB-4.4 | Slice-level assurance hook | Breach event | NB-4.2, MGT-18.2 |

#### PR-NB-5 — TM Forum adaptor

| Step | What | Done when | Needs |
|---|---|---|---|
| NB-5.1 | Choose the first API (TMF 641 service ordering) | ADR | – |
| NB-5.2 | Mapping between TMF order items and SO SMOS orders | Mapping tests | NB-5.1 |
| NB-5.3 | Routes and state mapping | Contract test | NB-5.2 |
| NB-5.4 | TMF event notifications | Receiver gets events | NB-5.3, MSG-1.4 |

#### PR-NB-6 — ONAP profile

| Step | What | Done when | Needs |
|---|---|---|---|
| NB-6.1 | Document which ONAP flows apply (VES, A1, O1) | Doc | – |
| NB-6.2 | Test VES into the SMO from an ONAP-style sender | Test green | SB-7.2 |

#### PR-NB-7 — SMO federation

| Step | What | Done when | Needs |
|---|---|---|---|
| NB-7.1 | ADR: trust and delegation model between two SMOs | ADR merged | SEC-10.1 |
| NB-7.2 | Peer registry | Migration; routes | NB-7.1 |
| NB-7.3 | Read-only cross-SMO inventory query | Test | NB-7.2, NB-2.2 |
| NB-7.4 | Delegated intent | Test | NB-7.2 |

### 5.11 AI/ML platform depth (`PR-AI`)

Current state, checked: AIMgF training, validation, emulation and inference jobs are completed from outside through
`CompleteJobRequest` (`succeeded`, `metrics`, and an output reference). There is no component here that runs a job.

#### PR-AI-1 — Executor protocol

| Step | What | Done when | Needs |
|---|---|---|---|
| AI-1.1 ★ | Document today's contract (start, notification, complete) as the executor protocol | Section in `aimgf/README.md` | – |
| AI-1.2 | `executor` registry table: name, URL, kinds supported | Migration | – |
| AI-1.3 | `executor` field on job requests (default: external, today's behaviour) | Existing tests green | AI-1.2 |
| AI-1.4 | On job start, POST the job spec to the executor URL (through the outbox) | Executor receives it | AI-1.3, MSG-1.4 |
| AI-1.5 | Reference executor container that completes jobs, replacing the demo scripts | Runbook uses it | AI-1.4 |
| AI-1.6 | Stuck-job detection: no completion within a timeout fails the job | Test | AI-1.3 |

#### PR-AI-2 — Kubernetes training executor

| Step | What | Done when | Needs |
|---|---|---|---|
| AI-2.1 | Image contract: inputs as env and mounts, outputs to a path | Doc | – |
| AI-2.2 | Job spec builder | Unit tests | AI-2.1 |
| AI-2.3 | Submit as a K8s `Job` | Runs on kind | AI-2.2, AI-1.4 |
| AI-2.4 | Status watch calls `complete` | Job result recorded | AI-2.3 |
| AI-2.5 | Upload the artifact to MLMR and set the output reference | Model artifact stored | AI-2.4 |
| AI-2.6 | Logs link stored on the job | Link works | AI-2.3 |
| AI-2.7 | GPU requests from the runtime profile | Pod spec shows them | AI-2.2 |

#### PR-AI-3 — MLflow bridge

| Step | What | Done when | Needs |
|---|---|---|---|
| AI-3.1 | Push training metrics to MLflow | Run visible | AI-1.4 |
| AI-3.2 | Register a model version on `CERTIFIED` | Version visible | AI-3.1 |
| AI-3.3 | Import an MLflow model into MLMR | Model appears | AI-3.2 |

#### PR-AI-4 — Serving adaptor

| Step | What | Done when | Needs |
|---|---|---|---|
| AI-4.1 | Interface for `RuntimeLifecycle` actions; today's behaviour is the default | Existing tests green | – |
| AI-4.2 | KServe `InferenceService` for deploy | Service ready on a cluster | AI-4.1, SB-16.2 |
| AI-4.3 | Status mapped to the runtime FSM | State follows readiness | AI-4.2 |
| AI-4.4 | Scale with the target size from `SB-17` | Replicas change | AI-4.2, SB-17.4 |
| AI-4.5 | Canary traffic split | Split visible | AI-4.2 |

#### PR-AI-5 — Feature store

| Step | What | Done when | Needs |
|---|---|---|---|
| AI-5.1 | Interface behind feature groups | Existing tests green | – |
| AI-5.2 | Feast adaptor | Group registered in Feast | AI-5.1 |
| AI-5.3 | Online read | Value returned | AI-5.2 |
| AI-5.4 | Materialise as a job | Offline data refreshed | AI-5.2, MSG-4.2 |

#### PR-AI-6 — Data sink for PM

| Step | What | Done when | Needs |
|---|---|---|---|
| AI-6.1 | Export a window of PM to Parquet in object storage | File readable | – |
| AI-6.2 | Incremental export with a watermark | No duplicates | AI-6.1 |
| AI-6.3 | Time-series DB sink | Data queryable | AI-6.2 |

#### PR-AI-7 — Drift and performance monitoring

| Step | What | Done when | Needs |
|---|---|---|---|
| AI-7.1 | Store baseline stats at training completion | Migration | – |
| AI-7.2 | Ingest performance reports from `MLMFSubscription` into a table | Rows appear | – |
| AI-7.3 | PSI and KS computation | Unit tests with known drift | AI-7.1 |
| AI-7.4 | Threshold breach raises an event | Event delivered | AI-7.3, MSG-1.4 |
| AI-7.5 | Flag the model and notify the owner | Flag visible in GUI | AI-7.4 |

#### PR-AI-8 — Weighted retrain triggers (`OI-1-weighted-triggers`)

| Step | What | Done when | Needs |
|---|---|---|---|
| AI-8.1 | Table of breach events per model | Rows from AI-7.4 | AI-7.4 |
| AI-8.2 | Analysis script over real breach data | Report | AI-8.1 |
| AI-8.3 | ADR for the weighting | ADR merged | AI-8.2 |
| AI-8.4 | Implement it; remove `NotImplementedError` | Test | AI-8.3 |

#### PR-AI-9 — Runtime lifecycle gate (`OI-6.1-runtime-gate`)

| Step | What | Done when | Needs |
|---|---|---|---|
| AI-9.1 | Decision on scope: which runtime transitions need approval | Decision in section 1 | – |
| AI-9.2 | Governance event and flag, same pattern as `APPROVE_DEPLOY` | Transition tests | AI-9.1 |
| AI-9.3 | Wire through `POST /models/{id}/advance` | Route test | AI-9.2 |
| AI-9.4 | GUI action | Component test | AI-9.3 |

#### PR-AI-10 — Action safeguards

| Step | What | Done when | Needs |
|---|---|---|---|

#### PR-AI-11 — Human approval of rApp actions

| Step | What | Done when | Needs |
|---|---|---|---|
| AI-11.1 | Approval request object | Migration | – |
| AI-11.2 | Queue routes: list, approve, reject | Route tests | AI-11.1 |
| AI-11.3 | Timeout policy: expire or auto-reject | Test | AI-11.2 |
| AI-11.4 | Hook into the autonomy-mode dispatch | Action waits for approval | AI-11.2 |
| AI-11.5 | Notification to approvers | Event delivered | AI-11.4, MSG-1.4 |

#### PR-AI-12 — Shadow mode

| Step | What | Done when | Needs |
|---|---|---|---|
| AI-12.1 | `shadow` flag per rApp instance | Migration | – |
| AI-12.2 | In shadow, route writes to an emulator endpoint, not O1 | No southbound call | AI-12.1 |
| AI-12.3 | Compare report of intended vs actual | Route test | AI-12.2 |

#### PR-AI-13 — Decision audit

| Step | What | Done when | Needs |
|---|---|---|---|
| AI-13.1 | Action record: inputs reference, model version, rationale, job id | Migration | – |
| AI-13.2 | Write it on every rApp config job | Row per job | AI-13.1 |
| AI-13.3 | Query route | Route test | AI-13.2 |
| AI-13.4 | GUI detail view | Component test | AI-13.3 |

### 5.12 rApp ecosystem (`PR-RAPP`)

#### PR-RAPP-1 — CSAR signing

| Step | What | Done when | Needs |
|---|---|---|---|
| RAPP-1.1 | Digest list of every file in the package | `build_csar.py` writes it | – |
| RAPP-1.2 | Detached signature (ed25519 or cosign) in the CSAR | Signature present | RAPP-1.1 |
| RAPP-1.3 | Trust store: accepted publisher keys from config | Config test | – |
| RAPP-1.4 | Verify in Onboarding validation | Tampered file rejected | RAPP-1.2, RAPP-1.3 |
| RAPP-1.5 | Policy flag: require signed packages | Unsigned rejected when on | RAPP-1.4 |
| RAPP-1.6 | Sign the committed sample CSARs | Integration test keeps passing | RAPP-1.2 |

#### PR-RAPP-2 — Runtime sandbox

| Step | What | Done when | Needs |
|---|---|---|---|
| RAPP-2.1 | Manifest runtime profile becomes CPU and memory limits in the NFO descriptor (confirm current mapping) **(verify)** | Descriptor shows them | – |
| RAPP-2.2 | Pod `securityContext` (non-root, no privilege escalation) | Pod spec shows it | SB-16.2 |
| RAPP-2.3 | Egress NetworkPolicy: rApp may reach R1 only | Other egress refused | OPS-2.6 |

#### PR-RAPP-3 — Conformance pack

| Step | What | Done when | Needs |
|---|---|---|---|
| RAPP-3.1 | Offline package validator CLI reusing Onboarding validation | Passes on samples | – |
| RAPP-3.2 | Runtime checks: register, heartbeat, R1 usage, terminate | Pass on a sample rApp | RAPP-3.1 |
| RAPP-3.3 | Report file | Generated | RAPP-3.2 |

#### PR-RAPP-4 — Java or Go SDK

| Step | What | Done when | Needs |
|---|---|---|---|
| RAPP-4.1 | Pick the language | Decision | – |
| RAPP-4.2 | Generate models and clients from `docs/openapi/` | Builds | RAPP-4.1 |
| RAPP-4.3 | Token acquisition and refresh | Test against the stack | RAPP-4.2 |
| RAPP-4.4 | One example rApp using it | Runs | RAPP-4.3 |
| RAPP-4.5 | CI build | Job green | RAPP-4.2 |

#### PR-RAPP-5 — Developer portal

| Step | What | Done when | Needs |
|---|---|---|---|
| RAPP-5.1 | Static site from the markdown docs and OpenAPI | Builds locally | – |
| RAPP-5.2 | Publish from CI | Site reachable | RAPP-5.1 |

#### PR-RAPP-6 — Usage metering

| Step | What | Done when | Needs |
|---|---|---|---|
| RAPP-6.1 | Per-invoker request and byte counters at R1 Termination | Visible on `/metrics` | OBS-2.2 |
| RAPP-6.2 | Daily roll-up table | Rows | RAPP-6.1 |
| RAPP-6.3 | Report route | Route test | RAPP-6.2 |

#### PR-RAPP-7 — New-rApp recipe

Repeat for each new rApp (anomaly detection, root cause, slice assurance, ...): copy `energy-saving-rapp`; model;
decision engine; `demo.py`; manifest and capabilities; CSAR build; unit tests; runbook section; call flow; entry in
the README tables. Each rApp is one piece of work per bullet, in that order.

### 5.13 GUI (`PR-GUI`)

#### PR-GUI-1 — Live updates

| Step | What | Done when | Needs |
|---|---|---|---|
| GUI-1.1 | SSE endpoint on the BFF, polling the source server-side and sending diffs | Client receives a change | – |
| GUI-1.2 | Client hook with reconnect | Test | GUI-1.1 |
| GUI-1.3 | Alarms page uses it | Alarm appears without reload | GUI-1.2 |
| GUI-1.4 | Instance and deployment state use it | Same | GUI-1.2 |
| GUI-1.5 | Source switches to the event bus | Same behaviour | GUI-1.1, MSG-3.5 |

#### PR-GUI-2 — Alarm console

| Step | What | Done when | Needs |
|---|---|---|---|
| GUI-2.1 | List with filters | Component test | MGT-8.4 |
| GUI-2.2 | Ack and clear actions with `rbac.py` rules | Role test | – |
| GUI-2.3 | Comments panel | Component test | MGT-8.3 |
| GUI-2.4 | History tab | Component test | MGT-8.2 |
| GUI-2.5 | CSV export | File content test | GUI-2.1 |

#### PR-GUI-3 — Topology view

| Step | What | Done when | Needs |
|---|---|---|---|
| GUI-3.1 | Graph API from the containment tree | Route test | SB-6.3 |
| GUI-3.2 | Viewer component | Renders demo data | GUI-3.1 |
| GUI-3.3 | Alarm overlay | Colours by severity | GUI-3.2 |
| GUI-3.4 | Drill-down to the element page | Test | GUI-3.2 |

#### PR-GUI-4 — KPI dashboards

| Step | What | Done when | Needs |
|---|---|---|---|
| GUI-4.1 | Chart of one KPI over time | Component test | MGT-11.5 |
| GUI-4.2 | Region filter | Test | GUI-4.1 |
| GUI-4.3 | Saved dashboard layouts per user | Test | GUI-4.1 |

#### PR-GUI-5 — Scoped views

| Step | What | Done when | Needs |
|---|---|---|---|
| GUI-5.1 | Scope claim in the session | Claim present | SEC-10.3 |
| GUI-5.2 | BFF adds the scope filter to proxied reads | Out-of-scope data absent | GUI-5.1 |

#### PR-GUI-6 — Accessibility and localization

| Step | What | Done when | Needs |
|---|---|---|---|
| GUI-6.1 | Automated accessibility check in the GUI tests | Runs in CI | – |
| GUI-6.2 | Fix findings per page | Zero serious findings | GUI-6.1 |
| GUI-6.3 | i18n library scaffold | One page translated | – |
| GUI-6.4 | Extract strings page by page | Per page: no literals | GUI-6.3 |

#### PR-GUI-7 — Approval inbox

| Step | What | Done when | Needs |
|---|---|---|---|
| GUI-7.1 | Inbox page listing pending change-window approvals | Component test | MGT-4.3 |
| GUI-7.2 | Add pending rApp action approvals | Component test | AI-11.2 |
| GUI-7.3 | Add model gate approvals | Component test | – |

### 5.14 Standards and compliance (`PR-STD`)

| Feature | Step | What | Done when | Needs |
|---|---|---|---|---|
| STD-1 | STD-1.1 | Close the §3 items (`SA-MLMR-1/6/7`, `SA-FOCOM-6/7`, `SA-RANOAM-1/4/8`, `SA-O1-4`); do not duplicate them here | §3 empty | – |
| STD-2 | STD-2.1 | Record the release of every spec in `specs/` | Table in `specs/README.md` | – |
| STD-2 | STD-2.2 | List newer releases and what changes for the SMO | List with item IDs | STD-2.1 |
| STD-3 | STD-3.1 | Map each interface to the O-RAN test specification | Table | – |
| STD-3 | STD-3.2 | Plugfest plan | One page | STD-3.1 |
| STD-4 | STD-4.1 | Inventory of personal data (GUI users, subscriber-derived PM) | Table | – |
| STD-4 | STD-4.2 | Retention per item | Linked to `DB-3` | STD-4.1, DB-3.1 |
| STD-4 | STD-4.3 | Erasure procedure for a GUI user | Tested once | STD-4.1 |
| STD-4 | STD-4.4 | Access logging for personal data reads | Rows appear | STD-4.1, SEC-11.2 |
| STD-5 | STD-5.1 | Control matrix (ISO 27001, NESAS/SCAS) against what exists | Matrix | SEC-14.2 |
| STD-6 | STD-6.1 | Data residency statement: where data lives and what leaves a site | One page | – |

### 5.15 Quality engineering (`PR-QA`)

#### PR-QA-1 — Load generator and baseline

| Step | What | Done when | Needs |
|---|---|---|---|
| QA-1.1 | Synthetic managed elements, cells, PM and alarms (scale knob) | Script seeds 1k elements | – |
| QA-1.2 | Seed script for large tables (used by `DB-4.2`) | 1M rows in under 10 minutes | QA-1.1 |
| QA-1.3 | Load script for the top routes (k6 or locust) | Runs against compose | QA-1.1 |
| QA-1.4 | Baseline numbers recorded at 1k, 10k and 100k elements | Table in `docs/` | QA-1.3 |

#### PR-QA-2 — Contract tests

| Step | What | Done when | Needs |
|---|---|---|---|
| QA-2.1 | Pilot: schemathesis or similar over one module's `docs/openapi/` file | Runs in CI | – |
| QA-2.2 | Consumer-side checks for cross-module calls made through `R1Client` | Break detected on a seeded change | – |
| QA-2.3 | Roll out to every module | Job covers all | QA-2.1 |

#### PR-QA-3 — Failure injection

| Step | What | Done when | Needs |
|---|---|---|---|
| QA-3.1 | Kill Postgres during a replay | Clean 503s and recovery | – |
| QA-3.2 | Kill SME during a replay | Same | – |
| QA-3.3 | Slow or dead webhook subscriber | Other calls unaffected | MSG-2.2 |
| QA-3.4 | Replica kills | See `HA-2` | HA-1.2 |

#### PR-QA-4 to QA-8

| Step | What | Done when | Needs |
|---|---|---|---|
| QA-4.1 | Upgrade test in CI: previous schema to head, then the replay | Job green | OPS-1.6 |
| QA-5.1 | 24-hour soak at baseline load | No memory or pool growth | QA-1.4 (OBS-2.4 done) |
| QA-5.2 | 72-hour soak | Same | QA-5.1 |
| QA-6.2 | Role matrix test for the GUI BFF (`rbac.py`) | Every rule has a positive and a negative test | – |
| QA-7.1 | `mllf` route tests (5 tests today, the CERTIFIED gate) | ≥ 20 route-level tests | – |
| QA-7.2 | Same for `ran-analytics`, `mock-o1-adaptor`, `so-smos`, `mock-near-rt-ric`, `r1-termination` | Counts raised, one PR each | – |
| QA-7.3 | Coverage floor in CI | Floor enforced | QA-7.1 |
| QA-8.1 | Nightly lane: NETCONF server, RIC simulator | Job green | SB-1.9, SB-13.2 |

### 5.16 Suggested first slices

Pick any, or mix them. `Needs` is the only constraint.

1. **Replica-safe foundation (no new infrastructure):** done.
2. **Safe to expose:** done (SEC-1.6 and SEC-13.2: `HISTORY.md` §10); SEC-13.4 follows the Helm chart.
3. **Operable:** done (OBS-1, OBS-2.1–2.3, OPS-1.1–1.5 and 1.7, OPS-4.1); open: OBS-2.8. OPS-1.6 done; OPS-4.1b done (`smo-v0.1.0`).
4. **Durable notifications:** MSG-1.1–1.10 done (SA SMOS has no destination call to move; the DME stop-job DELETE moved last, as a `DELETE` row).
5. **First real O1 path:** SB-3, SB-5.1–5.2, SB-1.1–1.2 done; SB-1.3 to 1.9 done (the netopeer2 lab answers the SSH wrapper, the route's read and a model write in CI).
6. **Safer changes:** done (MGT-1.1–1.8, MGT-3, MGT-8.1).
7. **Later:** HA, mesh, federation, vendor profiles.
8. **Dev sanity and demo:** OPS-10.1–10.4 done (the redeploy gate, `.github/workflows/deploy-on-main.yml`); OPS-11.1–11.4 (on-demand Codespaces demo, $0 spending limit) need nothing else; OPS-10.5 onward follows OPS-2 and OPS-5 (OPS-1.6 is done).

## v0.5.0 validation inventory (started)
- **Every CHECK constraint on a status/enum column against the code that writes it: done (V-5).** `tests_integration/test_check_constraints.py` covers all 97 single-column lists (ten state-machine columns by enum, the rest by assigned literals); computed values and multi-column checks are not covered.
- **The whole v0.5.0 validation plan** (by category, with the order of work, lanes and exit criteria) is `docs/VALIDATION.md`.
