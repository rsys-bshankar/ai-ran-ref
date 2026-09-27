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
    (planned next): 47 raw `HTTPException(status_code=..., detail="...")`
    call sites across 12 files bypass the existing RFC 7807
    `ProblemDetails`/`framework_error()` convention (`shared/smo_shared/
    errors.py`) — FOCOM uses it not at all. Asked rather than guessed
    given the real breaking-change cost (some existing callers read
    `resp.json()["detail"]` as a bare string for these specific
    endpoints): confirmed, fix all 47 now.
  - **Cross-cutting standardization, slice 3 of 3 — Pagination +
    Subscriptions** (planned after that): real limit/offset pagination
    (`{items, total, limit, offset}`) on every list endpoint across
    ~16 services — a real breaking change to GUI/SDK/tests_integration,
    confirmed rather than left as a documented-only finding. Subscription
    callback field names are unified only where no real external spec
    already fixes the name (DME/MDAF/Intent Service/A1-Related/AIMgF);
    FOCOM's `callback` (real O2ims) and SME's `callbackUri` (real CAPIF)
    stay as they are.

Reordering Wave 3 ahead of Wave 1/2, or starting new R1 contract design
before the ownership split is merged, is exactly the redesign-it-twice
risk this baseline exists to avoid.
