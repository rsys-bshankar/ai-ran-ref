# SMO AI Platform Architecture

This is the architecture of the SMO AI Platform in `smo/`: its layers, the
rules that hold it together, which standard each service realises, the
cross-cutting R1 conventions, and how the reference rApps use the platform.

Everything specific to one module (its ownership, design decisions, state
machines, data model, API, tests) lives in that module's own `README.md`,
linked from [Service ownership](#service-ownership). Changing a rule or an
ownership line is an architecture decision: edit this document or the module's
README first, then the code. Packaging of rApps is in
[RAPP_PACKAGING.md](RAPP_PACKAGING.md). For standards compliance matrices and
the runtime realization see [STANDARDS.md](STANDARDS.md); for how the platform
reached this shape see [HISTORY.md](../HISTORY.md).

## Contents

- [Layered architecture](#layered-architecture)
- [Golden rules](#golden-rules)
- [Process state and scale-out](#process-state-and-scale-out)
- [Service map](#service-map)
- [Repository layout](#repository-layout)
- [R1 API conventions](#r1-api-conventions)
- [Service ownership](#service-ownership)
- [Reference rApps](#reference-rapps)
- [Related documents](#related-documents)

## Layered architecture

```
+------------------------------------------------+
|                Operator / OSS                  |
+------------------------------------------------+
                    |  Intent Service
                    v
+================================================+
|                    SMO                         |
+================================================+
|           Platform Services Layer              |
|  SME | DME | MDAF | AIMgF | MLMR | MLLF        |
|  Intent Service | RAN NF OAM                   |
|  Onboarding | rApp Management                  |
+------------------------------------------------+
                    ^
                    |  R1 (R1 Termination)
                    v
+------------------------------------------------+
|             AI Runtime SDK Layer               |
|  sdk.data | sdk.analytics | sdk.models         |
|  sdk.lifecycle | sdk.intent | sdk.platform     |
+------------------------------------------------+
                    ^
                    v
+------------------------------------------------+
|                rApp Layer                      |
|  EnergySaving | Mobility Optimization          |
|  Coverage Optimization | Traffic Steering      |
|  hello-world (sample)                          |
+------------------------------------------------+
                    ^
                    v
+------------------------------------------------+
|           Runtime Execution Layer              |
|  TRAINING   -> MLTF    VALIDATION -> MLVF      |
|  EMULATION  -> MLEF    INFERENCE  -> MLIF      |
+------------------------------------------------+
                    ^
                    v
+------------------------------------------------+
|           Infrastructure Layer                 |
|  NFO | Kubernetes/Docker (unmodeled southbound)|
|  O2IMS/O2DMS (FOCOM) | GPU/NPU/CPU             |
+------------------------------------------------+
```

## Golden rules

1. **Platform Services are permanent services.** SME, DME, MDAF, AIMgF,
   MLMR, MLLF, Intent Service, RAN NF OAM, Onboarding and rApp Management
   are long-lived, independently deployed services, not modules of
   convenience.
2. **rApps are business logic.** The reference rApps under `samples/` are
   consumers of the platform, never platform internals.
3. **MLTF/MLVF/MLEF/MLIF are runtime roles, not services.** They are
   execution modes of one runtime (TRAINING → MLTF, VALIDATION → MLVF,
   EMULATION → MLEF, INFERENCE → MLIF), scheduled by NFO. There is no
   `mltf/`, `mlvf/`, `mlef/` or `mlif/` module. See
   [STANDARDS.md#runtime-realization](STANDARDS.md#runtime-realization).
4. **NFO owns execution placement.** Where and how a runtime executes is
   NFO's decision, never AIMgF's.
5. **AIMgF owns lifecycle.** AIMgF decides what state a model or runtime is
   in and whether a transition is allowed. It does not train, validate,
   emulate, infer or store anything itself.
6. **R1 owns service exposure.** Every platform service is reached through
   R1 Termination's gateway (`r1-termination/`). Nothing bypasses it; the AI
   Runtime SDK (`sdk/smo_sdk/`) is a thin client over that same path, not a
   second one.
7. **Service code is stateless.** A module keeps its state in Postgres, never
   in the process, and starts no background work, so any number of identical
   replicas can serve any request. Enforced by
   `scripts/check_statelessness.py` (CI job `lint`); the accepted exceptions
   are in [Process state and scale-out](#process-state-and-scale-out).

## Process state and scale-out

Every module is request-driven: no service starts a thread, timer, task
scheduler or background task, and each keeps its state in Postgres. The only
state a process holds is listed below. `scripts/check_statelessness.py` fails
the build on any new in-process state or background work in `<module>/app`,
`shared/smo_shared`, `sdk/smo_sdk` and `samples/*/app` (the `mock-*` test doubles
are out of scope), unless the finding is in
`scripts/statelessness_allowlist.txt` with a reason; an allowlist entry whose
finding has gone also fails, so the list cannot rot. Its limits: an ALL_CAPS
name is trusted to be a constant, and a class instance is recognised only when
its class is in the same file (so `R1Gateway` below is listed here, not detected).

### What a process holds today

| Holder | Where | What it is | Safe with N replicas? | Fix |
|---|---|---|---|---|
| `_identity` (`_ModuleIdentity`) | `shared/smo_shared/r1_client.py` | SME access-token cache and this process's invoker id and secret | Token cache: yes. Identity: no, each process onboards its own invoker at SME when `SMO_INVOKER_ID` is unset, so every replica and restart adds a registration | `PR-ST-4` |
| `app.state.login_failures` | `gui-bff/app/main.py` | Login-lockout counters per username | No, a replica does not see failures counted by another | `PR-ST-5` |
| JWT signing secret | `gui-bff/app/config.py` | Random per boot when `GUI_JWT_SECRET` is unset | No, a session cookie from one replica fails on another | `PR-ST-5` (set `GUI_JWT_SECRET` meanwhile) |
| `R1Gateway` token cache | `gui-bff/app/smo_client.py` | The BFF's SME token, refreshed once on a 401 | Yes. Its invoker credential is persisted in the database (`SmoCredential`), so replicas share one identity | none |
| `_builtin_schemas` (`lru_cache`) | `ran-nf-oam/app/vendors.py` | Bundled `cm_schemas/*.json`, read once | Yes, read-only and identical everywhere | none |
| `engine`, `SessionLocal` | `shared/smo_shared/db.py` | SQLAlchemy connection pool | Yes, a pool is per process by nature; sizing is `PR-ST-6` | none |
| FSM tables (`*_FSM`) | `<module>/app/statemachine.py` | Transition tables built once at import and never mutated | Yes | none |
| `_r1 = R1Client()` | most `main.py` | A thin client; it holds only a base URL | Yes | none |
| Module-level dicts and lists | `mock-near-rt-ric`, `mock-o1-adaptor` | Test-double state | Not applicable, they are test doubles | none |

What is **not** state: webhook destinations, subscriptions, jobs, FSM states,
registrations and every other business object are database rows.

Concurrency between replicas on the same row is a separate matter: FSM
transitions have no version check yet (`PR-ST-2`), and the inline retry in
`ran-nf-oam` holds a worker for its whole back-off (`PR-ST-9`).

### How time-driven behaviour starts

No module needs a periodic tick today. Everything that looks scheduled is
evaluated lazily on a request or pushed by an external caller, so there is
nothing to elect a leader for yet.

| Behaviour | Module | How it is triggered |
|---|---|---|
| Missed-heartbeat health of an O1 endpoint (`MISSED_HEARTBEAT_THRESHOLD`) | RAN NF OAM | Aged at the point of use: in `POST /o1-adaptor-endpoints/discover` and at the config-write gate (`_age_endpoint_health`) |
| A1 service keep-alive sweep (`keepAliveIntervalSeconds`) | A1 Related | A stale service and its policies are swept when `GET /services` reads it (`_sweep_stale_service`) |
| `upgradeTimeoutSeconds` | rApp Management | An overdue upgrade is rolled back the next time either row is touched (`expire_overdue_upgrade`) |
| Threshold monitors | SA SMOS | The caller posts `POST /monitors/{id}/evaluate` with current metrics; the service does not poll |
| Analytics report delivery | MDAF | Pushed to subscribers when a report is stored; otherwise the consumer polls `QueryAnalyticsReport` |
| `collectionInterval`, `reportInterval`, `heartbeatInterval` | FOCOM | Stored and validated only; nothing collects on a schedule (`SA-FOCOM-6`) |

The lazy sweeps above write to the database from a read, so two replicas can
both run the same sweep; that is another reason `PR-ST-2` matters.

A feature that needs a real periodic task (an alarm-aging sweep, a PM
collector, a drift check) must not add a thread or `create_task`: it needs the
single-runner helper of `PR-ST-8` or the job runner of `PR-MSG-4`.

## Service map

| Service | Module | Standard it realizes |
|---|---|---|
| SME | `sme/` | O-RAN (CAPIF-derived) |
| DME | `dme/` | O-RAN ICS-derived data plane + O1 Adaptor MnS mapping (O1 action mediation) |
| MDAF | `mdaf/` | 3GPP TS 28.104 (MDA NRM) |
| AIMgF | `aimgf/` | 3GPP TS 28.105 (AI/ML NRM): lifecycle, requests, functions |
| MLMR | `mlmr/` | 3GPP TS 28.105 (MLModel, repository) + TS 29.482 AIMLE MLR |
| MLLF | `mllf/` | TS 28.105 deploy-request gate and node-group targeting |
| Intent Service | `intent-service/` | 3GPP TS 28.312 (Intent NRM) |
| NFO | `nfo/` | O-Cloud / O2 (deployment) |
| FOCOM | `focom/` | O2IMS |
| RAN NF OAM | `ran-nf-oam/` | O1 (CM/FM/PM/SWM, per-vendor capability registry) |
| SO SMOS | `so-smos/` | O-RAN SMO-ARCH §4.2.7 SMOS (interfaces unspecified, internal design) |
| SA SMOS | `sa-smos/` | O-RAN SMO-ARCH §4.2.8 SMOS (interfaces unspecified, internal design); O1-CM handler is a 3GPP TS 28.312 RMIH |
| RAN Analytics | `ran-analytics/` | None (custom). A registry of analytics producers; reports go to MDAF, with no call between the two |

A1 policy (`a1-related/`) is a separate concept from intents and is not part
of the Intent Service.


## Repository layout

| Group | Modules |
|---|---|
| AI platform services | `aimgf/`, `mlmr/`, `mllf/`, `mdaf/`, `intent-service/`, `dme/` |
| Other platform services | `sme/`, `nfo/`, `focom/`, `ran-nf-oam/`, `onboarding/`, `rapp-mgmt/`, `a1-related/`, `sa-smos/`, `so-smos/`, `ran-analytics/` |
| Exposure | `r1-termination/` (R1 gateway), `sdk/` (AI Runtime SDK), `gui/` + `gui-bff/` |
| Southbound simulators | `mock-o1-adaptor/`, `mock-near-rt-ric/` |
| Shared library | `shared/smo_shared/` (DB, errors, pagination, correlation, webhook, R1 client, OpenAPI security) |
| rApps | `samples/` (four reference rApps + `hello-world-rapp`) |
| Tooling and tests | `scripts/`, `migrations/`, `tests_integration/` |

Each module directory holds `app/` (`models.py`, `statemachine.py`, `main.py`), `tests/`
(that module's standalone SQLite unit tests) and a `README.md` that is the
module's HLD, LLD and unit-test document.

**rApp packaging** (the CSAR layout, `manifest.yaml`, `capabilities.yaml`, and
what Onboarding validates) is documented in [RAPP_PACKAGING.md](RAPP_PACKAGING.md).

## R1 API conventions

Every R1-facing service applies the same conventions, implemented once in
`shared/smo_shared/`:

| Concern | Convention |
|---|---|
| Authentication | R1 Termination introspects every proxied bearer token against SME's issuer (RFC 7662). Each service's OpenAPI declares the `r1BearerAuth` HTTP-bearer scheme (`openapi_security.py`). Exempt at the gateway: `/health` and `/bootstrap` only; `/bootstrap` returns SME's own address for `/oauth2/token`, which is not proxied unauthenticated. The `r1BearerAuth` scheme is not declared on SME's own `/oauth2/token` and `/oauth2/introspect`. The southbound mocks (`mock-o1-adaptor`, `mock-near-rt-ric`) are not R1-facing. |
| Versioning | `info.version` is the R1 contract version (`R1_CONTRACT_VERSION`, `1.0.0`). |
| Errors | ProblemDetails-shaped bodies (`title`, `status`, `detail`; `type` is always `about:blank`, the error code is in `title`) raised via `framework_error()` / `FrameworkError` (`errors.py`) as an `HTTPException`, so the object arrives nested under a top-level `detail` key. R1 Termination and gui-bff answer flat `{title, status, detail}`. A1 policy management keeps its own A1 error table. |
| Pagination | Every DB-backed list returns `{items, total, limit, offset}` from a SQL `LIMIT`/`OFFSET` plus `COUNT(*)` (`pagination.py`). Exceptions: fixed enums (A1 `/policy-types`) and spec-fixed shapes (A1-PMS `/services`; CAPIF `GetApfIdServiceApis` / `DiscoverServices` in SME). The GUI's `useSmo()` and the SDK's `ensure_ok()` unwrap `items`. |
| Subscriptions | Subscription resources name their callback `notificationDestination`, unless a real external spec fixes another name (FOCOM `callback` per O2ims, SME `callbackUri` per CAPIF). One-off job callbacks (`InferenceJob.notificationDestination`, `TrainingJob.notificationUri`) are not subscriptions. |
| Callbacks | Any caller-supplied callback URL is called through `smo_shared.webhook`. |
| Correlation | `X-Correlation-ID` (`correlation.py`): middleware assigns one when absent; `R1Client` propagates it on every downstream call; R1 Termination forwards its own current id. It is not declared per operation in OpenAPI. See call flow 14. |
| Cross-module calls | Always `R1Client` through R1 Termination, never a direct service URL. |

## Service ownership

One line per service (the full ownership table, design decisions, state machines
and API are in each module's README):

| Service | Owns | README |
|---|---|---|
| AIMgF | State and decisions: model and runtime lifecycle, governance, NFO invocation | [aimgf](../aimgf/README.md) |
| MLMR | Model truth: identity, versions, artifacts, coordination groups | [mlmr](../mlmr/README.md) |
| MLLF | The deploy-request gate and node-group targeting | [mllf](../mllf/README.md) |
| NFO | Runtime truth: where and how a runtime executes | [nfo](../nfo/README.md) |
| MDAF | Analytics truth: reports, predictions, drift, TS 28.104 MDA (RAN Analytics only registers producers; it does not store or serve reports) | [mdaf](../mdaf/README.md) |
| DME | Data truth, plus O1 action mediation (O1 protocol dispatch is RAN NF OAM's) | [dme](../dme/README.md) |
| Intent Service | Intent truth: TS 28.312 intents, RMIH, autonomy dispatch | [intent-service](../intent-service/README.md) |
| RAN NF OAM | O1: CM / FM / PM / SWM dispatch, per-vendor capability registry (see its O1 vendor onboarding section) | [ran-nf-oam](../ran-nf-oam/README.md) |

The AI/ML responsibility matrix across AIMgF, MLMR and MLLF is in
[`aimgf/README.md`](../aimgf/README.md). Cross-module references are bare UUIDs
(for example `TrainingJob.model_id` into MLMR), resolved over R1 rather than by
reading another module's tables.

## Reference rApps

Four reference rApps under `samples/` exercise the platform end to end. Each
is a standalone CSAR (`manifest.yaml`, `capabilities.yaml`, ASD; four
execution modes, three autonomy modes, runtime profiles) and follows the same
pattern:

- **R1 only.** The rApp reaches the platform only through R1 Termination:
  the AI Runtime SDK (`smo_sdk.AiRuntimeSdk` over `R1Client`) plus plain R1
  reads of its rApp Management instance, RAN NF OAM alarms and peer rApps'
  published states. No A1, Near-RT RIC, xApps or E2.
- **O1 PM in.** PM reaches RAN NF OAM (`/pm-reports`), is registered as a DME
  type and read with `sdk.data.get_dataset`; a Digital Twin `*_SIM` producer
  feeds emulation. Guards come from `sdk.data.query_cell_guards` and alarms.
- **AI lifecycle.** Train, validate, emulate, certify and deploy through
  AIMgF / MLMR / MLLF / NFO; inference via
  `POST /aimgf/models/{id}/inference-jobs`.
- **O1 CM out.** A decision goes through `AutonomyDispatch` → Intent → SA SMOS
  O1-CM handler → DME `/actions` → RAN NF OAM `/config-jobs` → O1 adaptor,
  and is verified by read-back (`sdk.data.read_config` → `GET /ran-nf-oam/managed-entities/{me}/config`).
  KPI-driven reverts and rollbacks go straight to DME `/actions` with the
  execution's correlation id.
- **Coordination.** Mobility, Coverage and Traffic Steering read peer rApps'
  published states over R1 (peer instance ids in the instance config) so they
  do not act on the same cell or relation at once. Energy Saving relies on RAN NF
  OAM cell guards, neighbour load and alarms instead.

| rApp | Sample | O1 actuator(s) | Call flow |
|---|---|---|---|
| EnergySaving | [`samples/energy-saving-rapp/`](../samples/energy-saving-rapp/README.md) | `NRCellDU.administrativeState` or `CESManagementFunction.energySavingControl` (per instance) | [22](call-flows/22-energy-saving-closed-loop.md) |
| Mobility Optimization | [`samples/mobility-optimization-rapp/`](../samples/mobility-optimization-rapp/README.md) | `NRCellRelation.cellIndividualOffset` within `DMROFunction` bounds | [23](call-flows/23-mobility-optimization-closed-loop.md) |
| Coverage Optimization | [`samples/coverage-optimization-rapp/`](../samples/coverage-optimization-rapp/README.md) | `CommonBeamformingFunction.digitalTilt`, `NRSectorCarrier.configuredMaxTxPower` | [24](call-flows/24-coverage-optimization-closed-loop.md) |
| Traffic Steering | [`samples/traffic-steering-rapp/`](../samples/traffic-steering-rapp/README.md) | `NRFreqRelation.cellReselectionPriority` (idle), `NRCellRelation.cellIndividualOffset` (connected) | [25](call-flows/25-traffic-steering-closed-loop.md) |

Design decisions are in [STANDARDS.md](STANDARDS.md); how each was built and
reviewed (waves 10.1–10.4) is in [HISTORY.md](../HISTORY.md).

## Related documents

| Document | Content |
|---|---|
| Module READMEs (`<module>/README.md`) | HLD, LLD and unit tests of each module |
| [RAPP_PACKAGING.md](RAPP_PACKAGING.md) | rApp CSAR layout, manifest and capabilities, per-sample parameter tables |
| [STANDARDS.md](STANDARDS.md) | Frozen decisions, standards compliance matrices, runtime realization |
| [HISTORY.md](../HISTORY.md) | How the platform got here: decisions, audits, exit reviews (the code cites its IDs) |
| [call-flows/](call-flows/) | Sequence diagrams 01–27 (02 and 17: AI/ML lifecycle; 09: intents; 12: DME eligibility; 14: correlation id; 21: vendor onboarding; 22–25: reference rApps; 26: model governance and end of life; 27: TS 28.105 provisioning resources) |
| [openapi/](openapi/) | Generated OpenAPI specs per service |
| [`../DEMO_RUNBOOK.md`](../DEMO_RUNBOOK.md) | Runnable demo, including §24–§27 for the reference rApps |
