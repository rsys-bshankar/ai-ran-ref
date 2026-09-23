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
- **Intent-to-RMIH matching semantics** (`policy-mgmt/`) — `CreateIntent`
  and `RegisterIntentHandlingFunction` both exist, but nothing decides
  which RMIH a new Intent should be dispatched to; needs a definition of
  "matching" (by `intent_handling_scope`? capability equality? push
  notification or RMIH-side polling?) before it can be built. Surfaced
  writing call flow 09 (a sibling branch — see `smo/docs/call-flows/09-*`
  once merged).

## 2. Repo code / lifecycle gaps

- ~~**`NFDeploymentDescriptor` is never populated**~~ — **closed.** NFO
  now has a `CreateDescriptor` endpoint (`POST /descriptors`), called
  from Onboarding's `OnboardPackage` flow once validation succeeds;
  `rApp Management` consumes the real `nfDeploymentDescriptorId` via
  Onboarding's `onboarding-status` response instead of passing
  `packageId`. Verified against real Postgres (the FK now correctly
  rejects an invalid descriptor ID) and end to end via
  `tests_integration/test_cross_service.py`'s
  `test_onboarding_to_rapp_management_full_deploy_creates_real_nf_deployment_descriptor`.
- ~~`RAppInstance.RECOVER` had no HTTP route~~ — **closed.** The FSM
  transition (`FAULTED -> DEPLOYING`) existed and was unit-tested
  directly against the FSM, but no route in `rapp-mgmt/app/main.py`
  fired it — a critically faulted rApp instance had no API path back to
  `RUNNING`. Added `POST /instances/{id}/recover`, mirroring
  `bootstrap-complete`'s shape.
- ~~Onboarding's cascade-delete guard was unreachable from ordinary rApp
  deployment~~ — **closed.** `PackageUsageRegistration` rows were only
  ever created/stopped via Onboarding's `usage/start`/`usage/stop`,
  which nothing in `rApp Management`'s `CreateInstance`/`TerminateInstance`
  called. `CreateInstance` now calls `usage/start` and stores the real
  `registrationId` (new `RAppInstance.package_usage_registration_id`
  column); `TerminateInstance` now calls `usage/stop` before the row is
  deleted.
- ~~DME's `CreateDataJob` didn't validate against the actual
  `DataOffer`~~ — **closed.** It checked `dataDeliveryMethod` against the
  global `DELIVERY_METHODS` set only, never against what the
  `dmeTypeId`'s own `DataOffer` actually committed to. Now cross-checked
  (skipped when no offer exists for that type, so types without one are
  unaffected).
- **Two more real Postgres-schema bugs found fixing the above, same
  table** (`migrations/001_init.sql`'s `rapp_instance`), both fixed:
  `pending_upgrade_instance_id` was read/written throughout
  `rapp-mgmt/app/upgrade.py` and `main.py` but was **entirely missing**
  from the migration (only present in the SQLAlchemy model) — would
  crash on first use against real Postgres; and `oauth_client_id` was
  `NOT NULL` in the migration even though `_revoke_credential`
  explicitly sets it to `NULL` on `TERMINATE`/`UPGRADE_COMMIT` (closing
  v1.3's RT-3 finding) — would reject that commit outright. Neither was
  ever caught because SQLite's unit tests build their schema from the
  ORM models directly, never from this file, and the migration-Postgres
  CI job only checks table *count*, not columns. Verified fixed against
  a real local Postgres 16 instance.
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

## 3. Call-flow gaps

Only 4 of many plausible cross-module journeys are diagrammed today
(`smo/docs/call-flows/`): rApp onboarding → deployment, AI/ML model train
→ inference, config write with schema check, closed-loop assurance.
Missing:

- A1 EI registration end-to-end
- Onboarding failure / deprecation / deletion paths
- rApp fault/performance reporting
- RAN Analytics' own data-production flow
- Policy Mgmt Intent-driven flow
- A multi-step SO SMOS order combining INFRA + TRAINING + DEPLOY in one
  call chain

## 4. Test coverage is uneven

Per-module unit test counts:

| Module | Tests |
|---|---|
| mock-near-rt-ric | 5 |
| r1-termination | 5 |
| nfo | 5 |
| ran-analytics | 5 |
| focom | 7 |
| policy-mgmt | 7 |
| sa-smos | 8 |
| a1-related | 8 |
| onboarding | 9 |
| sme | 9 |
| rapp-mgmt | 9 |
| ran-nf-oam | 10 |
| ai-ml-workflow | 11 |
| dme | 13 |
| so-smos | 13 |

Plus 10 cross-service integration tests in `tests_integration/`.
`r1-termination` and `mock-near-rt-ric` are now the shallowest-covered
modules; `rapp-mgmt` and `dme` moved out of the shallow tier this pass
(both gained route-level tests — `rapp-mgmt` had none at all before).

## Closed

- **`NFDeploymentDescriptor` population** (§2, was priority 1) — see
  `smo/README.md`'s "Real bugs this pass found" section.
- **Bring the shallow-coverage modules to parity** (§4, was priority 2)
  — so-smos, ran-analytics, and focom now have route-level test
  coverage, not just dispatch/FSM-logic coverage. Writing it surfaced
  and fixed two real bugs: SO SMOS's `CancelOrder` never actually
  persisted (in-place JSON mutation SQLAlchemy never tracks), and RAN
  Analytics' `RegisterAnalyticsProducer` crashed on a legitimate
  re-registration (same shape as the SME bug from the original pass).
  See `smo/README.md`'s "Real bugs this pass found" section. `nfo` was
  separately brought to 5 by the `NFDeploymentDescriptor` fix.
- **Fill the call-flow gaps** (§3, was priority 3) — all six missing
  journeys are now in `smo/docs/call-flows/` (05 through 10); writing
  them is what surfaced the `RECOVER`/cascade-delete-guard/DME-validation
  items closed above, plus the Intent-to-RMIH matching item now in §1.

## Suggested next pass (priority order)

1. Resolve the design-level decisions (§1) that block further code — SA
   SMOS `RECONNECT`/`ROLLBACK` and RAN NF OAM's CM sync method remain the
   two most likely to unblock near-term code changes once decided; these
   genuinely need a stakeholder call, not an invented answer.
2. Deepen `r1-termination`'s and `mock-near-rt-ric`'s coverage — the
   shallowest tier remaining now that `rapp-mgmt` and `dme` have moved
   out of it.
