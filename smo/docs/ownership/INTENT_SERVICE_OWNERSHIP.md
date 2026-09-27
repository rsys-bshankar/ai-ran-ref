# Intent Service Ownership

Status: **implemented, Wave 3 architecture resolved**. See
`docs/architecture/SERVICE_OWNERSHIP_MATRIX.md` and
`docs/architecture/AI_PLATFORM_BASELINE.md`.

## Mission

Intent Service is intent truth. It realizes TS 28.312's real Intent NRM
(`specs/5G_APIs/TS28312_IntentNrm.yaml` + the `*Expectation.yaml`
files, already present and already audited — see `SPEC_AUDIT.md`'s
Policy Mgmt section).

## Owns

- `Intent`
- `IntentExpectation`
- `IntentReport`
- `IntentState`
- Intent resolution, intent assurance

## Correction to the source plan — read before Wave 1

The SMO Actions 1/2/3 documents describe this as splitting
`policy-mgmt` into a leftover "Policy Mgmt (Rule Engine: Policies/
Rules/Constraints)" plus a new `intent-service`. Checked against the
actual code before writing this: **that split has no basis in what
exists today.** `policy-mgmt/app/models.py` has exactly three classes —
`Intent`, `IntentReport`, `IntentHandlingFunction` — and
`policy-mgmt/app/main.py`'s entire route surface
(`/intents`, `/intents/{id}/admin-state`, `/intent-reports`,
`/intent-handling-functions`) is Intent-shaped. There is no
policy/rule/constraint model or route anywhere in this module to leave
behind. (A genuine "Policy" concept — `A1Policy`, real RIC policy
create/enforce/retract — already exists in this build, but it lives in
`a1-related/`, an entirely separate module this decomposition does not
touch.)

**Wave 1's actual action, therefore, is a rename, not an extraction**:
`policy-mgmt/` becomes `intent-service/` wholesale — same models, same
routes, same tests, moved and renamed to match the vocabulary this
document and `SERVICE_OWNERSHIP_MATRIX.md` use (`Intent` stays `Intent`,
etc. — no field-level rename forced by this move alone). If a real
Policy/Rule/Constraint concern is wanted under the SMO Design Document's
original "Policy Mgmt" framing, that is new functionality to design and
scope explicitly in a later wave, not something Wave 1 extracts from
existing code — and until it's asked for, `docs/architecture/
AI_PLATFORM_BASELINE.md`'s repository mapping should not claim a
surviving `policy-mgmt/` module exists.

## Does NOT own

| Concern | Owner |
|---|---|
| A1 policy create/enforce/retract (a genuinely different "policy" concept) | `a1-related/`, unchanged |

## Wave 3 resolution: consumer-side RMIH selection

The open item this section used to carry is resolved. TS 28.312's own
NRM containment model (`IntentHandlingFunction` *contains* `Intent`)
implied the spec's real answer was consumer-side LDN selection — an
MnS consumer picks and addresses an already-chosen RMIH when creating
an `Intent` — rather than this build's former producer-side push
matching (`create_intent`'s own `_matching_rmihs`, which scanned every
registered RMIH's declared capabilities and notified every match).
Asked rather than guessed, given the real breaking-change cost either
way: confirmed the redesign.

`CreateIntent` now requires a real, required `rmihId` field — the
caller names the specific, already-registered `IntentHandlingFunction`
this Intent is addressed to (discovered beforehand via
`GET /intent-handling-functions`). Real consequences of adopting the
containment model literally, not just cosmetically:

- `Intent.rmih_id` is a real foreign key onto
  `intent_handling_function.rmih_id`, `ON DELETE CASCADE` — a
  deregistered RMIH really does end every Intent still addressed to
  it, matching how a contained MOI cannot outlive its containing
  parent in real NRM/DN semantics (same house cascade-delete pattern
  used throughout this build elsewhere, e.g. `aiml_model` →
  `model_artifact`).
- `create_intent` still validates the named RMIH's declared
  capabilities/scope actually cover the Intent's request
  (`RMIH_CAPABILITY_MISMATCH`, 422) — addressing an Intent at a
  handler that can't fulfil it is rejected at creation, not silently
  un-notified the way the old multi-candidate filter left it.
- Dispatch is now a single best-effort notification to the one named
  RMIH, replacing the former broadcast-to-every-match loop.

## Migration source (Wave 1)

`policy-mgmt/app/{main.py,models.py,statemachine.py if any}` and
`policy-mgmt/tests/` move to `intent-service/` unchanged in content;
directory name and any internal `module_scope`/service-identity strings
update to match. Every existing caller of `policy-mgmt`'s routes
(`r1-termination`'s own routing table, any cross-service test) is
updated in the same PR.
