# AI Platform Architecture Baseline

Status: **frozen** — Wave 0 of the AI Platform Service Decomposition (see
`README.md`'s "Project status" for how this fits the overall build).
This is the official architecture from this point forward. Changing it
means editing this document and its siblings first, not just the code.

## Why this exists

`ai-ml-workflow/` today conflates three genuinely distinct
responsibilities — AI lifecycle orchestration, model repository, and
model loading/activation — into one flat service. That was a reasonable
Phase-1 simplification (see `SPEC_AUDIT.md`'s AI/ML Workflow section:
this build deliberately targeted the O-RAN-SC `aiml-fw`/`trainingmgr`
reference's flat shape rather than TS 28.105's full NRM containment
tree). This document reverses that choice deliberately: before any more
DME/MDAF/Intent contract work, the platform's real service boundaries
are frozen here, matching TS 28.105 (AI/ML), TS 28.104 (MDA), and
TS 28.312 (Intent) — not inferred per-PR, and not left to whichever
service happens to need a new field next.

No implementation changes land in this document's own commit. Wave 1
(service decomposition) is the first wave that touches code, and it
must not start until this baseline is merged.

## Layered architecture

```
+------------------------------------------------+
|                Operator / OSS                  |
+------------------------------------------------+
                    |
                    |  Intent Service
                    V

+================================================+
|                    SMO                         |
+================================================+

+------------------------------------------------+
|           Platform Services Layer              |
+------------------------------------------------+

 SME | DME | MDAF | AIMgF | MLMR | MLLF
 Intent Service | RAN NF OAM
 Onboarding | rApp Management

+------------------------------------------------+
                    ^
                    |
                    R1
                    |
                    V

+------------------------------------------------+
|             AI Runtime SDK Layer               |
+------------------------------------------------+

 sdk.data | sdk.analytics | sdk.models
 sdk.lifecycle | sdk.intent | sdk.platform

+------------------------------------------------+
                    ^
                    |
                    V

+------------------------------------------------+
|                rApp Layer                      |
+------------------------------------------------+

 EnergyOptimizer_rApp | MobilityOptimizer_rApp | CoverageOptimizer_rApp

+------------------------------------------------+
                    ^
                    |
                    V

+------------------------------------------------+
|           Runtime Execution Layer              |
+------------------------------------------------+

 TRAINING   -> MLTF
 VALIDATION -> MLVF
 EMULATION  -> MLEF
 INFERENCE  -> MLIF

+------------------------------------------------+
                    ^
                    |
                    V

+------------------------------------------------+
|           Infrastructure Layer                 |
+------------------------------------------------+

 NFO | Kubernetes/Docker (unmodeled southbound) | O2IMS/O2DMS (FOCOM) | GPU/NPU/CPU
```

## Golden rules

These are frozen. A change to any of them is a Wave-0-level architecture
decision, not a per-PR judgment call.

1. **Platform Services are permanent services.** SME, DME, MDAF, AIMgF,
   MLMR, MLLF, Intent Service (and the pre-existing RAN NF OAM,
   Onboarding, rApp Management) are long-lived, independently deployed
   services — not modules of convenience.
2. **rApps are business logic.** Energy Optimizer, Coverage Optimizer,
   Mobility Optimizer, and the sample `hello-world-rapp` are consumers
   of the platform, never platform internals.
3. **MLTF/MLVF/MLEF/MLIF are runtime roles, not services.** They are
   *execution modes* a single runtime takes on (TRAINING → MLTF,
   VALIDATION → MLVF, EMULATION → MLEF, INFERENCE → MLIF), scheduled by
   NFO. There is no `smo/mltf/` module and there will not be one.
4. **NFO owns execution placement.** Any decision about *where* and
   *how* a runtime actually executes belongs to NFO, never to AIMgF.
5. **AIMgF owns lifecycle.** AIMgF decides *what state* a model or a
   runtime is in and *whether* a transition is allowed. It does not
   train, validate, emulate, infer, or store anything itself — see
   `docs/ownership/AIMGF_OWNERSHIP.md`.
6. **R1 owns service exposure.** Every platform service is reached
   through R1 Termination's own gateway/routing (already real in this
   build — `r1-termination/`); nothing bypasses it, and the AI Runtime
   SDK (Wave 1) is a thin client over that same path, not a second one.

## Service ownership table

| Service | Spec/standard it realizes |
|---|---|
| SME | O-RAN (CAPIF-derived) |
| DME | O-RAN (ICS-derived data plane) + O1 Adaptor MnS mapping (control/actuation mediation, Wave 3 — `docs/ownership/DME_OWNERSHIP.md`) |
| MDAF | TS 28.104 (MDA) |
| AIMgF | TS 28.105 (AI/ML NRM) realization |
| MLMR | TS 28.105 (AI/ML NRM) + TS 29.482 AIMLE MLR (model repository/discovery, Wave 3 — `docs/ownership/MLMR_OWNERSHIP.md`) |
| MLLF | TS 28.105 (AI/ML NRM) realization — deploy-request gate + targeting only; the state machine itself is AIMgF+NFO's `RuntimeLifecycleState` (Wave 3 scope correction — `docs/ownership/MLLF_OWNERSHIP.md`) |
| Intent Service | TS 28.312 (Intent NRM) — consumer-side RMIH selection since Wave 3 (`docs/ownership/INTENT_SERVICE_OWNERSHIP.md`) |
| NFO | O-Cloud / O2 |
| FOCOM | O2IMS |
| RAN NF OAM | O1 |
| RAN Analytics | domain-specific analytics use cases, consumer of MDAF |

`policy-mgmt` does not appear in this table — see the correction note
below and `docs/ownership/INTENT_SERVICE_OWNERSHIP.md`.

Full owns/does-not-own detail for AIMgF, MLMR, MLLF, MDAF, DME, and
Intent Service lives in `docs/architecture/SERVICE_OWNERSHIP_MATRIX.md`
and the per-service docs under `docs/ownership/`.

## Repository mapping

**Existing components — keep as-is, this wave:** `sme/`, `dme/`,
`nfo/`, `focom/`, `ran-nf-oam/`, `onboarding/`, `rapp-mgmt/`,
`a1-related/`, `sa-smos/`, `so-smos/`, `r1-termination/`,
`mock-near-rt-ric/`, `mock-o1-adaptor/`.

**Narrowed, this decomposition:** `ran-analytics/` (keeps its
use-case-specific analytics, becomes an MDAF consumer rather than the
analytics-owning service).

**Split apart, this decomposition:** `ai-ml-workflow/` → `aimgf/` +
`mlmr/` + `mllf/` (Wave 1).

**Renamed, not extracted — a correction to the source plan:**
`policy-mgmt/` → `intent-service/`. The SMO Actions 1/2/3 documents
describe this as an extraction leaving a "Policy Mgmt" rule engine
behind, but `policy-mgmt/` today has no policy/rule/constraint code at
all — every model and route in it (`Intent`, `IntentReport`,
`IntentHandlingFunction`) is already Intent-shaped, confirmed by reading
the actual module before writing this document. See
`docs/ownership/INTENT_SERVICE_OWNERSHIP.md` for the full correction. A
real Policy/Rule/Constraint engine, if wanted, is new work for a later
wave, not a migration — and `a1-related/`'s own genuine `A1Policy`
concept (real RIC policy create/enforce/retract) is unrelated and
unaffected.

**New, this decomposition:** `mdaf/` (out of `ran-analytics/`'s prior
scope), `sdk/` (a new client layer with no prior equivalent).

## Sequencing

Frozen wave order — do not reorder without updating this document first:

- **Wave 0** (this document + its siblings): architecture freeze. No code.
- **Wave 1**: service decomposition — `aimgf`/`mlmr`/`mllf`/`mdaf`/
  `intent-service`/`sdk` created, existing callers migrated. Structure
  and ownership, not new business logic. Also extends rApp packaging
  with two new optional CSAR-root files, `manifest.yaml`/
  `capabilities.yaml` (Onboarding's `_validate_package`,
  `onboarding/app/main.py`), so a package can declare which of `sdk/`'s
  six namespaces it consumes or provides — additive only, a package
  without either file (every one built before this extension) onboards
  unchanged.
- **Wave 2** (done): AIMgF's own two real state machines (`ModelLifecycleState`,
  14 states; `RuntimeLifecycleState`, 8 states, jointly owned with NFO)
  and its full eight-aggregate domain model, deepened past Wave 1's
  structural split — see `docs/ownership/AIMGF_OWNERSHIP.md`.
- **Wave 3** (in progress): R1 contracts (DME → MDAF → MLMR → AIMgF →
  MLLF → Intent Service, in that order), OpenAPI skeletons, sequence
  diagrams, and cross-cutting OpenAPI standardization (OAuth2/JWT,
  Correlation-ID, Error Schema, Versioning, Pagination, Subscriptions) —
  last, once every contract's shape is already stable.
  - **DME slice** (done): revises DME from a pure data-job/offer broker
    into a dual data-plane + O1-actuation-mediation service, with real
    source/vendor provenance and a Digital-Twin-excluded-from-inference
    eligibility rule enforced at the DB layer — see
    `docs/ownership/DME_OWNERSHIP.md`. Real O1 protocol dispatch stays
    in `ran-nf-oam/`; DME's `/actions` route mediates and forwards to
    it, it does not duplicate it. MDAF's report inputs are now validated
    against real DME artifacts, closing the "MDAF sources from DME only"
    rule with enforced code rather than a documented convention.
  - **MDAF slice** (done): closes `SPEC_AUDIT.md`'s TS28.104
    `ThresholdInfo` finding — a subscription with declared thresholds is
    now notified only on a real `UP`/`DOWN`/`UP_AND_DOWN` crossing with
    genuine hysteresis state, not on every report. `analytics_type`'s
    free-string-vs-`MDAType`-enum finding was audited (every real value
    this build uses is informal shorthand, no clean mapping) and left
    open by that explicit finding, not closed.
  - **MLMR slice** (done): grounds MLMR against a second, more directly
    applicable spec — 3GPP TS 29.482 AIMLE's `MLR_MLModelManagement`/
    `MLR_ModelInformationDiscovery` (a real model-repository/discovery
    Stage-3 API, newly cataloged in `specs/README.md`) — alongside the
    original TS28.105 NRM audit. Adds real `domain`/`customDomain`/
    `vendors` (the last echoing DME's own multi-vendor provenance
    principle, applied to model identity) and `ModelArtifact.size_bytes`
    computed from the actual uploaded bytes. `MLModelPhase` confirmed
    NOT a gap to close on MLMR — that's exactly the lifecycle concept
    Wave 2 moved to AIMgF; re-adding it to MLMR would regress that
    decision. See `SPEC_AUDIT.md`'s own MLMR section for the full
    closed/deferred breakdown.
  - **AIMgF slice** (done): closes the AI/ML Workflow audit's
    `cancelRequest`/`suspendRequest` finding — `TrainingJob` gains a
    `SUSPENDED` status plus `POST .../suspend`/`.../resume` routes, a
    plain status flip that deliberately does not become a third state
    machine (Wave 2's two real FSMs are untouched by it). The
    `requestStatus` vocabulary-mismatch finding is reconfirmed still
    open — only `SUSPENDED` itself was adopted, not a full rename of
    the remaining values, which would be a real breaking change.
  - **MLLF slice** (done, doc-only): MLLF was still a Wave-1 stub (one
    gate route, no models of its own) whose ownership doc described a
    "load/unload/activate/deactivate + deployment record" surface as
    deferred future work. That work turned out to already be done —
    Wave 2's `RuntimeLifecycleState` (AIMgF+NFO) answers exactly that
    question. Rather than build a second, competing state machine,
    `docs/ownership/MLLF_OWNERSHIP.md` and this baseline's own service
    table/matrix are corrected to say so explicitly; MLLF's code is
    unchanged (it was already only ever the gate+targeting surface this
    correction describes).
  - **Intent Service slice** (done): resolves the architecture question
    this baseline itself flagged for Wave 3 — TS28.312's own NRM
    containment (`IntentHandlingFunction` *contains* `Intent`) implies
    consumer-side RMIH selection, not the former producer-side push
    matching. Asked rather than guessed, given the real breaking-change
    cost: redesigned `CreateIntent` to require a real, required
    `rmihId` — the caller addresses one already-registered RMIH
    directly, validated against its declared capabilities/scope
    (`RMIH_CAPABILITY_MISMATCH`, 422) rather than silently filtered
    around. `Intent.rmih_id` is a real `ON DELETE CASCADE` FK — a
    deregistered RMIH now genuinely ends every Intent still addressed
    to it, matching real NRM containment literally. GUI, SDK, and the
    demo runbook's own step 10 all updated to match.
  - **Cross-cutting standardization, slice 1 of 3 — OAuth2/JWT +
    Versioning** (done): grounded against the real code first —
    `r1-termination/app/main.py`'s own `_authorized()` already does real
    RFC 7662 introspection against SME's token issuer on every proxied
    request (OPEN_ITEMS.md section 2's "no real OAuth2/token
    enforcement" was already closed), but not one of the 17 R1-facing
    services' generated OpenAPI specs declared any security scheme, and
    every one still carried FastAPI's untouched default
    `info.version: "0.1.0"`. New `shared/smo_shared/openapi_security.py`
    declares an `r1BearerAuth` HTTP-bearer scheme and a real
    `info.version: "1.0.0"` on each of the 17 in-scope specs, purely at
    the OpenAPI-schema level — no enforcing dependency added to any
    individual service (they still all trust r1-termination's gateway,
    exactly as before; this only makes that already-real interface
    honest in its own contract). SME's own `/oauth2/token` and
    `/oauth2/introspect`, and r1-termination's own `/health` and
    `/bootstrap`, are the declared exemptions. `mock-near-rt-ric`/
    `mock-o1-adaptor` are deliberately excluded — they simulate external
    O1/A1 southbound endpoints with their own separate real auth model,
    not R1-facing services. Zero runtime behavior change: every
    service's full unit suite (574 tests across 18 modules) and the
    full `tests_integration/` suite pass unchanged.
  - **Cross-cutting standardization, slice 2 of 3 — Error Schema**
    (done): all 47 raw `HTTPException(status_code=..., detail="...")`
    call sites across 12 files (a1-related, aimgf, dme, focom,
    intent-service, mllf, mlmr, nfo, rapp-mgmt, sa-smos, sme, gui-bff —
    FOCOM used none of the shared convention at all) now go through
    `framework_error()`/`FrameworkError` (23 new, specifically-named
    `*_NOT_FOUND`/`ARTIFACT_FORMAT_INVALID` codes added to
    `shared/smo_shared/errors.py`) or, for gui-bff's own separate local
    convention (four `Depends()`-raised auth checks that must `raise`,
    not `return` — `current_session`/`require_admin`), a new
    `_problem_exception()` sharing `_problem()`'s own body shape. Checked
    for real breaking-change impact before shipping, not assumed: the
    GUI's `describeError` already handled both the old bare-string shape
    and the new `{title, status, detail}` shape gracefully (verified by
    reading `gui/src/api/client.ts` directly) — strictly better
    rendering, no regression. Two genuinely stale mocks/fakes were found
    and fixed in the same PR: `aimgf/tests/test_main.py`'s `FakeMlmr`
    double now returns MLMR's real new 404 shape (nothing in aimgf's own
    code reads the body, only the status code, so this was cosmetic);
    the SDK's own `test_raises_sdk_error_on_a_4xx_response` tests were
    confirmed to test generic SDK error-passthrough mechanics with an
    arbitrary scripted body, not any specific real endpoint's shape, so
    left alone deliberately. Zero real behavior regression: every
    service's full unit suite (574 tests), the full `tests_integration/`
    suite, and the GUI's typecheck + vitest all pass.
  - **Cross-cutting standardization, slice 3a — Pagination** (done):
    real limit/offset pagination (`{items, total, limit, offset}`,
    `shared/smo_shared/pagination.py`'s `paginate()`) on every
    DB-backed list endpoint across all 16 backend services — confirmed
    as a real breaking change rather than left undone. A real SQL
    `LIMIT`/`OFFSET` plus a real `COUNT(*)` for `total`, never a
    Python-level slice of an already-fetched full result set. Two
    classes of route deliberately excluded, grounded case by case
    rather than converted uniformly: (1) routes backed by a fixed,
    non-DB enum (A1-Related's `/policy-types`) — nothing to paginate;
    (2) routes whose response shape is dictated by a real external spec
    checked directly against its own YAML — A1-Related's `/services`
    (`{"serviceList": [...]}`, real A1-PMS `pms-api-v3.json` shape) and
    SME's `/published-apis/v1/{apf_id}/service-apis` +
    `/service-apis/v1/allServiceAPIs` (real CAPIF `GetApfIdServiceApis`/
    `DiscoverServices` operations). SME's own
    `/capif-events/v1/{subscriber_id}/subscriptions` GET was checked
    against the real `TS29222_CAPIF_Events_API.yaml` and confirmed to
    not exist there at all (only `POST` does) — this build's own GUI-pass
    addition, so paginated like everything else.

    GUI: rather than touch ~90 call sites, `gui/src/api/hooks.ts`'s
    `useSmo()` now auto-unwraps `{items: [...]}` at the one shared fetch
    boundary every list-reading call already goes through, so every
    existing `T[]`-typed call site keeps working unchanged; the two
    direct `smo()` calls outside `useSmo` (`Dashboard.tsx`,
    `Flows.tsx`) call the same exported `unwrapPage()` helper. One real
    exception: `/aimgf/feature-groups`'s old custom
    `{featureGroups: [...]}` wrapper is gone now that every list route
    shares one convention — its one call site and the SDK's
    `list_feature_groups()` return-type annotation both updated to
    match. SDK: `sdk/smo_sdk/_common.py`'s
    `ensure_ok()` (the one shared response helper every `sdk.*` method
    already goes through) unwraps the same way, so every
    `list_*`/`query_*`/`discover_*` method's own `-> list[dict]`
    annotation stays correct without touching each method.

    Verified: every service's full unit suite (594 tests across 18
    modules, plus new/updated tests where a list route's shape changed
    — a real SQLAlchemy `Session` doesn't enforce `LIMIT`/`OFFSET`
    server-side any differently in SQLite vs Postgres, so this needed
    no separate live-Postgres check the way some earlier slices did),
    the SDK's own suite (83 tests), the full `tests_integration/` suite
    against real Postgres 16 (31/31, including the OpenAPI-drift check
    against the now-larger specs — every paginated route gained real
    `limit`/`offset` query parameters), `scripts/
    check_migration_matches_models.py`, `docker compose config`, and
    the GUI's typecheck + vitest (49/49).
  - **Cross-cutting standardization, slice 3b — Subscriptions** (done):
    subscription callback field names unified to `notificationDestination`
    only on genuine Subscription-shaped resources, and only where no real
    external spec already fixes a different name — never as a blanket
    rename. DME's `TypeSubscription` and A1-Related's
    `PolicyStatusSubscription`/`ServiceRegistration` were already at the
    target convention (no change needed). FOCOM's `callback` (real O2ims)
    and SME's `callbackUri` (real CAPIF) are deliberately left as they are
    — already grounded in the Pagination slice's own spec-fidelity work,
    and real spec-fixed names always win over uniformity. Two real changes:
    Intent Service's `IntentHandlingFunction.notificationCallbackUri` is
    renamed to `notificationDestination` (grepped: the real
    `TS28312_IntentNrm.yaml` never names this field at all, so this is this
    build's own invention, free to unify — includes a real migration
    column rename). MDAF's `subscribe_analytics` moves
    `notification_destination` from a query param into
    `SubscribeAnalyticsRequest`'s body as `notificationDestination` — the
    one subscription-shaped resource in this build that had taken its
    callback outside the body. AIMgF's `MLMFSubscription` — audited and
    deliberately left open, not built: it declares no callback field at
    all and has no unsubscribe route, but adding either would be building
    new functionality, not unifying an existing field name, and so falls
    outside this slice's scope (same precedent as the MDAF slice's own
    `analytics_type` enum finding: audited, named, left open).
    `AIMgF/InferenceJob.notificationDestination` and
    `TrainingJob.notificationUri` are deliberately untouched — one-off
    job-completion callbacks, not subscription resources, and outside the
    audited Subscriptions scope.

    GUI, SDK (`sdk/smo_sdk/intent.py`'s
    `register_intent_handling_function`, `sdk/smo_sdk/analytics.py`'s
    `subscribe`), and `tests_integration/test_demo_runbook.py`/
    `DEMO_RUNBOOK.md`'s own runnable snippets all updated to match.
    Verified: intent-service, mdaf, and sdk unit suites; the full
    `tests_integration/` suite against real Postgres 16;
    `scripts/check_migration_matches_models.py` (Intent Service's renamed
    migration column); `docker compose config`; the GUI's typecheck +
    vitest.

This closes Wave 3 in its entirety — all six R1-contract service slices
(DME, MDAF, MLMR, AIMgF, MLLF, Intent Service) and all three cross-cutting
standardization slices (OAuth2/JWT + Versioning, Error Schema, Pagination +
Subscriptions) described at the top of this section are now done.

Reordering Wave 3 ahead of Wave 1/2, or starting new R1 contract design
before the ownership split is merged, is exactly the redesign-it-twice
risk this baseline exists to avoid.
