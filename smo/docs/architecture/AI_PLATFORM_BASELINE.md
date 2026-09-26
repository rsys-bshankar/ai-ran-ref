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
| DME | O-RAN (ICS-derived) |
| MDAF | TS 28.104 (MDA) |
| AIMgF | TS 28.105 (AI/ML NRM) realization |
| MLMR | TS 28.105 (AI/ML NRM) realization |
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
  and ownership, not new business logic.
- **Wave 2**: AIMgF's own two state machines (Model Lifecycle, Runtime
  Lifecycle) and domain model, deepened past Wave 1's structural split.
- **Wave 3**: R1 contracts (DME → MDAF → MLMR → AIMgF → MLLF → Intent
  Service, in that order), OpenAPI skeletons, sequence diagrams, and
  cross-cutting OpenAPI standardization (OAuth2/JWT, Correlation-ID,
  Error Schema, Versioning, Pagination, Subscriptions) — last, once
  every contract's shape is already stable.

Reordering Wave 3 ahead of Wave 1/2, or starting new R1 contract design
before the ownership split is merged, is exactly the redesign-it-twice
risk this baseline exists to avoid.
