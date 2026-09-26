# Intent Service Ownership

Status: **frozen** — Wave 0. See `docs/architecture/SERVICE_OWNERSHIP_MATRIX.md`
and `docs/architecture/AI_PLATFORM_BASELINE.md`.

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

## Open item carried into Wave 3

TS 28.312's own NRM containment model (`IntentHandlingFunction`
*contains* `Intent`) implies the spec's real answer is consumer-side LDN
selection — an MnS consumer picks and addresses an already-chosen RMIH
when creating an `Intent` — rather than this build's current
producer-side push matching (`create_intent`'s own `_matching_rmihs`).
This was already flagged as an open architecture question in
`SPEC_AUDIT.md` before this decomposition; Intent Service's Wave 3
contract design is the point to resolve it explicitly, not carry the
ambiguity forward under a new name.

## Migration source (Wave 1)

`policy-mgmt/app/{main.py,models.py,statemachine.py if any}` and
`policy-mgmt/tests/` move to `intent-service/` unchanged in content;
directory name and any internal `module_scope`/service-identity strings
update to match. Every existing caller of `policy-mgmt`'s routes
(`r1-termination`'s own routing table, any cross-service test) is
updated in the same PR.
