# Open Items — Phase 1 SMO Reference Build

Tracked gaps against the original scope (`SMO Design Document v1.3`, the
`AI-RAN Framework Consolidated Reference`, and the `O-RAN-SC Repo
Inventory`), beyond what PR #1 delivers. Grouped so the next pass can be
picked up module by module. Nothing here blocks PR #1 — CI is green and it
merges as Phase 1's baseline; this is the Phase 2 backlog.

## 1. Design-level decisions needed (not just code)

These need a call from whoever owns the relevant module's requirements
before they can be implemented — inventing an answer now would just move
the ambiguity into code.

- **In-flight training-job upgrade behavior** (`ai-ml-workflow/`) — what
  happens to a `TrainingJob` mid-flight when the underlying `AIMLModel` is
  upgraded. Left ambiguous in the LLD.
- **rApp-as-producer reconsideration trigger** (`dme/` / `rapp-mgmt/`) —
  when a `RAppInstance` itself acts as a DME producer, what re-evaluates
  its registration on state change. Flagged, not decided.
- **`MLModelCoordinationGroup` × SA SMOS convergence** (`ai-ml-workflow/`
  / `sa-smos/`) — how a coordination group's retrain propagation and SA
  SMOS's remedial-action dispatch are meant to interact. Deferred.
- **`WEIGHTED_TRIGGERS`** (`ai-ml-workflow/`) — currently raises
  `NotImplementedError`. Needs real noise-floor data to design the
  weighting function; the value would be fabricated without it.
- **Shape B / `JOINT_TRAINING`** (`ai-ml-workflow/`) — declared out of
  scope for this build; never revisited to confirm that's still correct.
- **RAN NF OAM's CM cache sync method** (`ran-nf-oam/`) — RESTCONF vs.
  NETCONF confirmation owed to the O1 Adaptor team, never closed.
- **Alarm-storm correlation algorithm** (`ran-nf-oam/`) — flagged as
  needing a real algorithm; nothing implemented.
- **A1-ML operations** (`a1-related/`) — categorically out of scope per
  the A1 Related LLD section 0; schema-dormant. Revisit only if that scope
  decision changes.
- **SA SMOS `RECONNECT`/`ROLLBACK`** (`sa-smos/`) — raises a clear error
  rather than guessing at one of several plausible remedial-action
  meanings. Needs the intended semantics defined.
- **`upgradeTimeoutSeconds` default (300s)** (`rapp-mgmt/`) — a
  placeholder, not a researched value.

## 2. Repo code / lifecycle gaps

- **`NFDeploymentDescriptor` is never populated** (`nfo/` + `onboarding/`)
  — the NFO+FOCOM LLD's own design says it should be derived from an
  onboarded package's TOSCA `Definitions/` at onboarding time; nothing in
  this build creates one. `rApp Management` currently passes `packageId`
  directly where NFO expects a real descriptor ID. Only surfaces against
  real Postgres FK enforcement (SQLite's test engine doesn't catch it) —
  flagged explicitly in `tests_integration/test_cross_service.py`. Fix:
  new NFO endpoint to create the descriptor, called from Onboarding's
  `OnboardPackage` flow.
- **No real southbound integrations beyond the A1 mock** — O1 Adaptor
  `PATCH` calls, actual `docker run` invocations, etc. are all elided in
  favor of recording the correct state transition.
- **No real OAuth2/token enforcement at R1 Termination** — only a comment
  and a `tokenEndPoint` URI in the bootstrap response; no actual
  validation code path.
- **RAN NF OAM's MnS Registry discovery is a heartbeat-aging stub**, not
  real registry polling.
- **No persisted OpenAPI spec files anywhere** — relying entirely on
  FastAPI's live `/docs` generation rather than committed contracts.
- **FOCOM's hardcoded single-cluster stub.**
- **NFO's Heal/Scale operations are stubs.**
- **None of the Repo Map's ADOPT recommendations are actually
  vendored/integrated** — this build consolidates on one Python/FastAPI
  stack rather than forking `nonrtric-plt-sme` (Go), the ICS reference
  (Java), `pti-o2` (Python), etc. The ADOPT repos stay pattern references
  only.
- **The full `docker-compose` stack (15 services, including the isolated
  `a1_mock_net` network segment) has never been run end-to-end** — no
  Docker daemon is available in the build sandbox; only
  `docker-compose config` YAML parsing and direct Python/pytest execution
  against each service in isolation have been validated. The RT-7
  network-isolation claim is structurally correct in the compose file but
  functionally unverified.
- **`RAppInstance.RECOVER` has no HTTP route** (`rapp-mgmt/`) — the FSM
  transition (`FAULTED -> DEPLOYING`) exists and is unit-tested directly
  against the FSM, but no route in `app/main.py` fires it; a critically
  faulted rApp instance has no API path back to `RUNNING`. Fix: a
  `POST /instances/{id}/recover` route mirroring `bootstrap-complete`'s
  shape. Surfaced writing call flow 07.
- **Onboarding's cascade-delete guard is unreachable from ordinary rApp
  deployment** (`onboarding/` + `rapp-mgmt/`) — `PackageUsageRegistration`
  rows are only ever created/stopped via Onboarding's `usage/start`/
  `usage/stop`, which nothing in `rApp Management`'s `CreateInstance`/
  `TerminateInstance` calls. Fix: wire those two calls into the
  respective rApp Management routes. Surfaced writing call flow 06.
- **DME's `CreateDataJob` doesn't validate against the actual `DataOffer`**
  (`dme/`) — it checks `dataDeliveryMethod` against the global
  `DELIVERY_METHODS` set only, not against what the `dmeTypeId`'s own
  `DataOffer` actually committed to; a consumer can request a method the
  producer never offered. Surfaced writing call flow 05.
- **Policy Mgmt has no Intent-to-RMIH matching/dispatch step**
  (`policy-mgmt/`) — `CreateIntent` and `RegisterIntentHandlingFunction`
  both exist, but nothing notifies an RMIH of a new Intent it could
  fulfil; `IntentHandlingFunction.intent_handling_scope` is modeled but
  no code path sets or reads it. Surfaced writing call flow 09.

## 3. Call-flow gaps — closed

~~Only 4 of many plausible cross-module journeys were diagrammed~~ — all
six missing journeys listed here previously are now in
`smo/docs/call-flows/` (05 through 10): A1 EI registration end-to-end,
onboarding failure/deprecation/deletion paths, rApp fault/performance
reporting, RAN Analytics' own data-production flow, the Policy Mgmt
Intent-driven flow, and a multi-step SO SMOS order combining
INFRA + TRAINING + DEPLOY.

Writing them surfaced four real, previously-undocumented gaps (now each
its own item in section 2 below, not fixed here — these are doc-writing
findings, not doc-writing fixes):

- `RAppInstance`'s `RECOVER` transition (`FAULTED -> DEPLOYING`) has no
  HTTP route — a critically-faulted rApp instance has no API path back
  to `RUNNING` at all (call flow 07).
- Onboarding's cascade-delete guard depends on `PackageUsageRegistration`
  rows that rApp Management's `CreateInstance`/`TerminateInstance` never
  actually creates or stops — `usage/start`/`usage/stop` are reachable
  only out-of-band, not from ordinary rApp deployment (call flow 06).
- DME's `CreateDataJob` validates `dataDeliveryMethod` against the
  global known-methods set only, never against the specific `DataOffer`
  the `dmeTypeId` is actually associated with (call flow 05).
- Policy Mgmt has no matching/dispatch step between `CreateIntent` and
  `RegisterIntentHandlingFunction` — an RMIH is never notified of a new
  Intent it could fulfil; `IntentHandlingFunction.intent_handling_scope`
  is modeled but no code path ever sets or reads it (call flow 09).

## 4. Test coverage is uneven

Per-module unit test counts:

| Module | Tests |
|---|---|
| so-smos | 3 |
| ran-analytics | 3 |
| focom | 4 |
| nfo | 4 |
| mock-near-rt-ric | 5 |
| r1-termination | 5 |
| rapp-mgmt | 5 |
| onboarding | 7 |
| policy-mgmt | 7 |
| sa-smos | 7 |
| a1-related | 8 |
| sme | 9 |
| dme | 10 |
| ran-nf-oam | 10 |
| ai-ml-workflow | 11 |

Plus 9 cross-service integration tests in `tests_integration/`. The
shallower modules (so-smos, ran-analytics, focom, nfo) have basic
CRUD/validation coverage but not the same depth of edge-case and
failure-path testing the FSM-heavy modules got.

## Suggested next pass (priority order)

1. `NFDeploymentDescriptor` population (§2) — closes a real cross-module
   correctness gap already caught by an integration test, self-contained,
   no open design question blocking it.
2. Bring up the shallow-coverage modules (§4: so-smos, ran-analytics,
   focom, nfo) to parity with the rest.
3. ~~Fill the call-flow gaps (§3)~~ — done; see §3.
4. The four small, self-contained gaps §3 surfaced while writing those
   flows (§2: `RAppInstance.RECOVER`'s missing route, the cascade-delete
   guard's dead usage-registration wiring, DME's unchecked
   `DataOffer`/`DataJob` method mismatch, Policy Mgmt's missing
   Intent-to-RMIH dispatch) — each is independent, no open design
   question blocking any of them, same shape as item 1 above.
5. Resolve the design-level decisions (§1) that block further code — SA
   SMOS `RECONNECT`/`ROLLBACK` and RAN NF OAM's CM sync method are the two
   most likely to unblock near-term code changes once decided.
