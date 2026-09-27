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
| MLLF | TS 28.105 (AI/ML NRM) realization |
| Intent Service | TS 28.312 (Intent NRM) |
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

Reordering Wave 3 ahead of Wave 1/2, or starting new R1 contract design
before the ownership split is merged, is exactly the redesign-it-twice
risk this baseline exists to avoid.
