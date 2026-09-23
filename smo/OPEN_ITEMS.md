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

- **`WEIGHTED_TRIGGERS`** (`ai-ml-workflow/`) — currently raises
  `NotImplementedError`. Needs real noise-floor data to design the
  weighting function; the value would be fabricated without it.
- **Alarm-storm correlation algorithm** (`ran-nf-oam/`) — flagged as
  needing a real algorithm; nothing implemented.
- **A1-ML operations** (`a1-related/`) — categorically out of scope per
  the A1 Related LLD section 0; schema-dormant. Revisit only if that scope
  decision changes.

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

## 3. Call-flow gaps — closed

~~Only 4 of many plausible cross-module journeys were diagrammed~~ — all
six missing journeys listed here previously are now in
`smo/docs/call-flows/` (05 through 10): A1 EI registration end-to-end,
onboarding failure/deprecation/deletion paths, rApp fault/performance
reporting, RAN Analytics' own data-production flow, the Policy Mgmt
Intent-driven flow, and a multi-step SO SMOS order combining
INFRA + TRAINING + DEPLOY.

Writing them surfaced four real, previously-undocumented gaps. Three
are closed above (§2: `RAppInstance.RECOVER`'s missing route, the
cascade-delete guard's dead usage-registration wiring, DME's unchecked
`DataOffer`/`DataJob` method mismatch); the fourth (Policy Mgmt's
Intent-to-RMIH matching) turned out to need its own design decision and
moved to §1 instead:

- `RAppInstance`'s `RECOVER` transition (`FAULTED -> DEPLOYING`) had no
  HTTP route — a critically-faulted rApp instance had no API path back
  to `RUNNING` at all (call flow 07). Closed.
- Onboarding's cascade-delete guard depended on `PackageUsageRegistration`
  rows that rApp Management's `CreateInstance`/`TerminateInstance` never
  actually created or stopped — `usage/start`/`usage/stop` were reachable
  only out-of-band, not from ordinary rApp deployment (call flow 06). Closed.
- DME's `CreateDataJob` validated `dataDeliveryMethod` against the
  global known-methods set only, never against the specific `DataOffer`
  the `dmeTypeId` is actually associated with (call flow 05). Closed.
- Policy Mgmt has no matching/dispatch step between `CreateIntent` and
  `RegisterIntentHandlingFunction` — an RMIH is never notified of a new
  Intent it could fulfil; `IntentHandlingFunction.intent_handling_scope`
  is modeled but no code path ever sets or reads it (call flow 09). Needs
  a design decision — see §1.

## 4. Test coverage is uneven

Per-module unit test counts:

| Module | Tests |
|---|---|
| nfo | 9 |
| ran-analytics | 9 |
| mock-near-rt-ric | 10 |
| r1-termination | 10 |
| policy-mgmt | 10 |
| rapp-mgmt | 12 |
| sa-smos | 12 |
| focom | 13 |
| so-smos | 13 |
| dme | 15 |
| ai-ml-workflow | 18 |
| onboarding | 18 |
| a1-related | 18 |
| sme | 18 |
| ran-nf-oam | 20 |

Plus 10 cross-service integration tests in `tests_integration/`.
`nfo`/`ran-analytics`/`sme` (9 tests each) are now the
shallowest-covered tier. `rapp-mgmt` and
`dme` moved out of the shallow tier in an earlier pass (both gained
route-level tests); a later pass added route-level coverage for the five
§1 items closed below — `ai-ml-workflow` (+4), `policy-mgmt` (+3),
`sa-smos` (net +2, replacing one parametrized "ambiguous" test with four
RECONNECT/ROLLBACK-specific ones), and `ran-nf-oam` (+10: its first
route-level tests at all, plus unit tests for the new NETCONF client).
This pass closed the test-coverage priority item: `r1-termination` gained
coverage for non-GET methods, body/header/query-param forwarding, and
non-200 upstream passthrough (previously only GET and the URL-stripping
fix were exercised); `mock-near-rt-ric` gained coverage for
`UpdatePolicy` (`PUT /a1-p/policies/{id}`), which had zero tests at all
before this pass despite being a real route.

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
- **Five §1 design-level decisions, resolved by the stakeholder and
  implemented:**
  - **In-flight training-job upgrade behavior** (`ai-ml-workflow/`) — a
    second `RequestTraining` call on a model already `TRAINING` now
    cancels the orphaned job rather than silently overwriting
    `model.training_job_id`; the operator's new call wins. Also fixed a
    real crash bug found while implementing this: `RequestTraining`
    always fired the `TRAIN` event regardless of model state, which is
    only a legal transition from `REGISTERED` — every ordinary retrain
    (`ACTIVE -> TRAINING`) crashed with an unhandled `IllegalTransition`.
    Now fires `TRAIN` or `RETRAIN` based on the model's actual state.
  - **Intent-to-RMIH matching semantics** (`policy-mgmt/`) — resolved as
    capability-based push: `CreateIntent` takes an `intentType`, matches
    it against each `IntentHandlingFunction`'s
    `intent_handling_capability_list`, and POSTs to the new
    `notificationCallbackUri` (DME's `producerHealthCallbackUrl` pattern)
    of every match. Best-effort — an unreachable RMIH callback never
    fails `CreateIntent` itself.
  - **RAN NF OAM's CM cache sync method** (`ran-nf-oam/`) — confirmed
    NETCONF. `WriteConfigurationChanges`'s per-change dispatch (previously
    elided behind a comment that recorded every sub_change as `APPLIED`
    without dispatching anything) now sends a real NETCONF-shaped
    `<edit-config>` RPC (`netconf_client.py`) over plain HTTP to the ME's
    `O1AdaptorEndpoint.adaptor_uri` — not real SSH/ncclient transport,
    matching this build's all-HTTP-JSON pragmatism everywhere else. An ME
    provisioned for RESTCONF (`ManagedEntity.o1_protocol`) has no
    dispatch implementation yet and is rejected with the (previously
    dormant) `PROTOCOL_NOT_SUPPORTED` rather than silently applied.
    Scoped to the CM-write path only — the separate `cm_schema_cache`
    fetch path is untouched.
  - **SA SMOS `RECONNECT`/`ROLLBACK`** (`sa-smos/`) — split into two
    different problems. `RECONNECT` is now resolved: it reads the
    `AssuranceMonitor`'s `target_order_id` back from SO SMOS's own order
    record to find the completed `DEPLOY` step's `nfDeploymentId`, then
    dispatches to NFO's Heal — no new resource-reference field needed on
    the monitor itself. `ROLLBACK` stays unsupported, but now for a
    concrete, checked reason (new `ROLLBACK_HISTORY_UNAVAILABLE`, 501)
    instead of a generic "ambiguous meaning" refusal: rApp Management's
    own upgrade machinery deletes the prior `RAppInstance` row on a
    successful commit, so there is no version history anywhere in this
    build to roll back to — an rApp Management data-retention gap, not
    an SA SMOS design question. A coordination-group-scoped
    `RECONNECT`/`ROLLBACK` isn't resolved by this pass either — only the
    `targetOrderId` path is; bundled with the still-deferred
    `MLModelCoordinationGroup` × SA SMOS convergence item above.

  See `smo/docs/call-flows/04-closed-loop-assurance.md` for the updated
  RECONNECT/ROLLBACK sequence.
- **Deepen `r1-termination`'s and `mock-near-rt-ric`'s coverage** (§4, was
  priority 2) — both sat at 5 tests, the shallowest tier remaining after
  the previous pass. `r1-termination`'s proxy only ever had GET exercised
  through it, with no assertion on body/header/query-param forwarding or
  on a non-200 upstream response passing through unchanged (only the
  URL-stripping fix and the route table itself were covered); now also
  covers POST body+method forwarding, `Host` header stripped while others
  pass through, a 503 upstream response reaching the caller unchanged,
  the prefix-with-no-trailing-segment edge case, and that `/dme`,
  `/dme-push`, `/dme-pull` route independently rather than colliding.
  `mock-near-rt-ric`'s `UpdatePolicy` (`PUT /a1-p/policies/{id}`) had zero
  test coverage at all — a real, callable route nothing exercised; now
  covered for the enforced/rejected/unknown-id cases, plus idempotent
  delete-of-unknown-id and cross-policy isolation. Both now at 10 tests,
  no real bugs found (the gap was pure absence of tests, not a latent
  defect). 163 tests total, up from 153.
- **Deepen `nfo`'s and `ran-analytics`' coverage** (§4, was priority 2) —
  both sat at 5 tests, the shallowest tier remaining after the previous
  pass. `nfo`'s `query_operation_status` (`GET /operations/{id}`) had
  zero test coverage at all — Instantiate's and Heal/Scale's own
  `LCMOperation` rows were written but never read back through the route
  meant to query them; `query_cluster_placement`'s success path was also
  never asserted, only its deleted-deployment error path. Both now
  covered, plus idempotent `terminate` on an unknown deployment id.
  `ran-analytics`'s `RegisterAnalyticsProducer` never asserted on its own
  SME registration side-effect payload (the `mdaf.<analyticsType>`
  naming, `producerId`, `moduleScope`); `unsubscribe_analytics` on an
  unknown id and `query_analytics_report`'s unfiltered (no
  `analytics_type`) path were both also untested. All now covered,
  including `MDAFReport.scope`, previously never set to a non-`None`
  value in any test. No real bugs found this pass either — same pure
  absence-of-tests shape as the previous coverage pass. Both now at 9
  tests; `focom` (7) is now the shallowest-covered module. 171 tests
  total, up from 163.
- **Deepen `focom`'s coverage** (§4, was priority 2) — sat at 7 tests,
  the shallowest tier remaining after the previous two passes. The
  unfiltered path of `GET /performance` (no `resource_ref`) was never
  exercised, only the filtered case; `GET /inventory`'s default
  `resourceTypeId` fallback (`"generic"`) was asserted nowhere, only the
  explicit-`resource_type` case; `GET /alarms` and `GET /performance`'s
  empty-list responses were never checked; only one alarm was ever
  ingested in any test, never proving a second one doesn't overwrite or
  drop the first; and `deprovision_resource`'s Phase 1 stub behavior
  (succeeds for any `resource_id`, provisioned or not) was only ever
  exercised incidentally through the provision-then-deprovision happy
  path, never asserted directly. All six now covered. No real bugs found
  — same pure absence-of-tests shape as the previous two coverage
  passes. Now at 13 tests; `a1-related` (8) is now the shallowest-covered
  module. 177 tests total, up from 171.
- **Deepen `a1-related`'s coverage** (§4, was priority 2) — sat at 8
  tests, the shallowest tier remaining after the previous three coverage
  passes. Five whole routes had zero test coverage at all: `GET
  /policies/{id}`, `PUT /policies/{id}`, `DELETE /policies/{id}`,
  `DELETE /policies/subscriptions/{id}`, and `DELETE /ei-types/{id}` — by
  far the largest single-pass gap found across all four coverage passes.
  All five now covered, including each delete route's idempotent-on-
  unknown-id case, `DELETE /policies/{id}` actually reaching the (mocked)
  Near-RT RIC via `a1_termination_client` rather than only dropping the
  local mirror row, and `query_policy`'s unguarded-`None` crash on an
  unknown/deleted id explicitly asserted (the same "genuine error path,
  not a friendly 404" pattern `nfo`'s own placement route uses) rather
  than silently avoided. No real bugs found — same pure absence-of-tests
  shape as the previous three coverage passes. Now at 18 tests — the
  largest module by test count after `ran-nf-oam`.
  `onboarding`/`sme`/`rapp-mgmt`/`nfo`/`ran-analytics` (9 tests each) are
  now the shallowest-covered tier. 187 tests total, up from 177.
- **Four more §1 design-level decisions, resolved by the stakeholder and
  implemented:**
  - **rApp-as-producer reconsideration trigger** (`dme/` / `rapp-mgmt/`)
    — resolved as a push: `rapp-mgmt`'s `RAppInstance` FSM now calls a
    new `DELETE /production-capabilities` DME route (keyed by
    `producer_id`, which is the instance's own `oauth_client_id` —
    bootstrap registers with SME/DME under that same identity) whenever
    an instance crashes or terminates, deregistering every DME type it
    produced. Best-effort, same "unreachable callback never fails the
    primary operation" precedent as Policy Mgmt's `CreateIntent`
    dispatch — a DME outage never blocks CRASH/TERMINATE themselves.
    `UPGRADE_COMMIT` is deliberately untouched: the replacement
    instance's own `oauth_client_id` is never set by `start_upgrade` in
    the first place, a separate, pre-existing gap out of this decision's
    scope.
  - **`MLModelCoordinationGroup` × SA SMOS convergence**
    (`ai-ml-workflow/` / `sa-smos/`) — resolved as: retrain is the only
    meaningful remedial action for a coordination group, wired on both
    sides. `report_performance` used to compute `groupRetrainTriggered`
    and stop — nothing ever fired `RETRAIN` on a member model; it now
    fires the same `ACTIVE -> TRAINING` transition `RequestTraining`'s
    own `modelId`-targeted path uses for every currently-`ACTIVE`
    member, creating a per-model `TrainingJob` each. On the SA SMOS
    side, a coordination-group-scoped `AssuranceMonitor`
    (`target_coordination_group_id`) now bypasses `CONFIG_CHANGE`/
    `SCALE`/`RECONNECT`/`ROLLBACK` entirely — those are NF-deployment
    concepts that don't map onto a model group — and always dispatches
    a group retrain via AI/ML Workflow's `RequestTraining` instead,
    regardless of the requested `actionType`.
  - **Shape B / `JOINT_TRAINING`** (`ai-ml-workflow/`) — the original
    v1.3 scope decision (out of scope for this build) is confirmed
    still correct, not just left unrevisited. The schema still permits
    `group_type='JOINT_TRAINING'` for forward compatibility, but no
    code branches on it and none is expected to this phase.
  - **`upgradeTimeoutSeconds` default (300s)** (`rapp-mgmt/`) —
    confirmed as the actual intended default, not a placeholder standing
    in for missing data. The "ungrounded"/"placeholder" language is
    removed from the model column comment and the migration; the value
    itself (300) is unchanged.

  Implementing the second item's test coverage surfaced two more real,
  previously-latent bugs in `report_performance`'s group lookup, neither
  ever caught because no test had exercised that code path before:
  `MLModelCoordinationGroup.member_model_ids.any(model.model_id)` is real
  Postgres `ANY(array)` SQL with no SQLite equivalent under
  `member_model_ids`' JSON fallback (`no such function: ANY`) — replaced
  with an in-Python membership filter; and that filter itself needed a
  string comparison, not `model.model_id in group.member_model_ids`
  directly, since SQLite's JSON fallback has no UUID item type and reads
  `member_model_ids` back as plain strings where Postgres's native
  `ARRAY(Uuid)` round-trips real `uuid.UUID` objects. New tests: `dme`
  (+2), `rapp-mgmt` (+3), `ai-ml-workflow` (+3), `sa-smos` (+2). 197
  tests total, up from 187.
- **Deepen `onboarding`'s coverage** (§4, was priority 2) — the largest
  single-pass coverage gap found yet: 6 of its 8 routes (`GET /packages`,
  `POST .../deprecate`, `POST .../cancel-delete`, `DELETE /packages/{id}`,
  `POST .../usage/start`, `POST .../usage/{id}/stop`) had zero test
  coverage at all, including the cascade-delete guard the module's own
  docstring calls out as important — neither half of it (a blocking
  dependent child package, an active usage registration) had ever been
  exercised. All six now covered, along with `_validate_package`'s real
  validation logic against genuinely malformed zip bytes (every prior
  test mocked the function away entirely) and the `onboarding-status`
  404-shaped-but-actually-409 error path for an unknown package id. No
  real bugs found — same pure absence-of-tests shape as every coverage
  pass so far. Chosen over the three other tied modules
  (`sme`/`nfo`/`ran-analytics`) after a scan of all four route
  inventories found `onboarding` had by far the most uncovered routes.
  Now at 18 tests (was 9, tied for the largest module by test count).
  `nfo`/`ran-analytics`/`sme` (9 tests each) are the new shallowest tier.
  206 tests total, up from 197.
- **Deepen `sme`'s coverage** (§4, was priority 2) — the `nfo`/
  `ran-analytics`/`sme` survey flagged `sme` as the likely candidate,
  since `nfo` and `ran-analytics` were already close to thoroughly
  covered. `DELETE /capif-events/v1/{subscriber}/subscriptions/{id}`
  (`unsubscribe_events`) had zero test coverage at all; `deregister_service`'s
  wrong-producer no-op guard, and `discover_services`' `api_name`/
  `api_version` query filters, were also untested. `notify_service_change`
  — real event-type-filtering and best-effort-delivery logic, gated by
  the same authz check `discover_services` uses — isn't wired into any
  route yet (its own docstring calls this out: "wired in as a
  follow-up"), so it had never been tested at all; now covered directly
  rather than left untested until something calls it. No real bugs
  found — same pure absence-of-tests shape as every coverage pass so
  far. Now at 18 tests (was 9). `nfo`/`ran-analytics` (9 tests each) are
  the new shallowest tier, both already close to thoroughly covered per
  the earlier survey. 215 tests total, up from 206.

## Suggested next pass (priority order)

1. The three remaining §1 design-level decisions — `WEIGHTED_TRIGGERS`,
   the alarm-storm correlation algorithm, and A1-ML operations — are not
   stakeholder-answerable the way the rest of this section was: the
   first two genuinely need real data (noise-floor data; a real
   correlation algorithm) that would otherwise be fabricated, and the
   third only needs revisiting if A1-ML's out-of-scope decision itself
   changes. Not blocked on a call, blocked on data or a scope change.
2. `nfo` and `ran-analytics` are now tied as the shallowest-covered tier
   (9 tests each), but an earlier survey found only 1-2 minor edge-case
   gaps in each — already close to thoroughly covered. Diminishing
   returns: a coverage pass here would likely find little. Worth a
   quick look only if another pass like this one is specifically wanted.
