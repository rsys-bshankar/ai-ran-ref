# Mock Near-RT RIC (`mock-near-rt-ric/`)

> An in-memory A1-P test double that gives A1 Related something real to call so a policy's `enforcementStatus` can move away from `PENDING`.

| | |
|---|---|
| Standards basis | Test double of an O-RAN Near-RT RIC A1-P endpoint (not a standard component) |
| R1 route / port | None: not R1-facing and not routed by R1 Termination. Container :8000 on the isolated `a1_mock_net` compose network, no published ports |
| Depends on (over R1) | none |
| Called by | A1 Related only (`a1-related/app/a1_termination_client.py`, over plain HTTP) |
| Database tables | none (in-memory dicts) |
| Unit tests | 16 passed (`tests/`, standalone) |
| Status | Done. Deliberately not a real A1AP implementation (see [section 2.8](#28-limits-and-open-items)) |

## 1. High-level design (HLD)

### 1.1 Purpose and scope

A1 Related's confirmed sequence for CreatePolicy includes an A1 call to a Near-RT RIC. This build has no RIC, so this service stands in for it: it accepts policy create / update / delete / status calls and answers with an enforcement status. Without it, the status mirror in A1 Related would stay `PENDING` forever.

It is a test and demo double, not an SMO module. Its scope is the minimum needed to exercise A1 Related's success, rejection and not-found paths.

### 1.2 Standards basis

| Spec | What is realised | What is deliberately not |
|---|---|---|
| O-RAN A1-P policy interface (shape only; no A1 spec file is bundled in [`../../specs/`](../../specs/README.md)) | Create / update / delete / status of a policy instance under `/a1-p/policies`; an enforcement-status answer | No A1AP wire conformance: no A1TD policy schema validation, no policy types or type definitions, no A1-EI, no A1-ML, no A1TP transport security (TLS + mTLS + OAuth2.0 + JWT); paths and parameters are this project's own |
| O-RAN-SC `near-rt-ric-simulator` (pattern reference) | The duplicate-policy-content check, using a content fingerprint scoped by policy type | The simulator's other behaviours |

### 1.3 Position in the platform

```
 A1 Related --HTTP (a1_mock_net)--> mock-near-rt-ric
```

- A1 Related is on both the default and `a1_mock_net` networks and is the only caller. The mock is on `a1_mock_net` only, with no published ports, so no other module (and nothing reaching R1) can call it. This is the endpoint-isolation requirement of the SMO design.
- It calls nothing and has no database.

### 1.4 Ownership

| Owns | Does not own → owner |
|---|---|
| The simulated policy store and fingerprint set (process memory) | Policy mapping store, policy-type catalog, status mirror → A1 Related |
| The simulated enforcement decision | Real enforcement → a real Near-RT RIC |

### 1.5 Design decisions

- **Deterministic outcomes for tests.** An empty `policyObject` is `REJECTED`; a policy whose content duplicates another policy's content of the same type is `REJECTED`; everything else is `ENFORCED`.
- **Fingerprint = type-scoped, key-order-independent content.** `json.dumps(policy_object, sort_keys=True) + ":" + policy_type_id`. Identical content under a different type is not a duplicate. The duplicate check spans every stored fingerprint, regardless of `near_rt_ric_id`.
- **Rejections are answers, not HTTP errors.** Every operation returns 2xx with an `enforcementStatus` (and `rejectionReason`); there are no error responses.
- **Server-generated ids.** `policyId` is always a fresh UUID, so the reference's "reused id across types" check does not apply.
- **No persistence, no auth.** State is lost on restart; none is needed.

## 2. Low-level design (LLD)

### 2.1 Code map

| File | Responsibility |
|---|---|
| `app/main.py` | The whole service: four routes and two module-level dicts |

### 2.2 Data model

None: in-memory.

| Store | Shape |
|---|---|
| `_policies` | `policy_id` -> `{nearRtRicId, policyTypeId, status}` (status `ENFORCED` or `REJECTED`) |
| `_fingerprints` | `policy_id` -> content fingerprint; present only while the policy is enforced with content |

A rejected-at-create policy is stored in `_policies` (so its status can be queried) but has no fingerprint.

### 2.3 State machines

None: stateless with respect to lifecycle. A policy's `status` is overwritten by the latest create / update outcome.

### 2.4 API

Not R1-facing; not in the R1 route table. Paths are under `/a1-p`.

| Method | Path | Purpose / responses |
|---|---|---|
| POST | `/a1-p/policies` | Query `near_rt_ric_id`, `policy_type_id`; body is the policy object. 201 `{policyId, enforcementStatus, rejectionReason}`: `REJECTED` (`empty policyObject` or `duplicate policy content for this type`) or `ENFORCED` |
| PUT | `/a1-p/policies/{policy_id}` | Body is the new policy object. Unknown id: `{enforcementStatus: REJECTED, rejectionReason: "unknown policyId"}` (200). Empty object: `REJECTED`, fingerprint dropped. Content equal to a different policy's: `REJECTED` (status overwritten, own old fingerprint kept). Otherwise `ENFORCED` and the fingerprint is replaced; updating to its own current content is not a collision |
| DELETE | `/a1-p/policies/{policy_id}` | 204; idempotent; frees the content fingerprint for reuse |
| GET | `/a1-p/policies/{policy_id}/status` | `{policyId, enforcementStatus}`; an unknown id answers `{enforcementStatus: SUSPENDED, rejectionReason: "unknown policyId"}` |

The OpenAPI document is [`../docs/openapi/mock-near-rt-ric.json`](../docs/openapi/mock-near-rt-ric.json) (3 paths).

### 2.5 Interactions

Inbound HTTP from A1 Related only. No outbound calls, callbacks or background tasks.

### 2.6 Configuration

None. No environment variables are read. The container listens on :8000 (shared Dockerfile `uvicorn app.main:app`).

### 2.7 Error codes

None: no ProblemDetails are produced. Failure is expressed as `enforcementStatus` `REJECTED` (create / update) or `SUSPENDED` (status of an unknown id).

### 2.8 Limits and open items

- **Not A1AP.** No policy types, no A1TD schema validation, no A1TP security, no notifications, no multi-RIC awareness (`near_rt_ric_id` is stored, never used for isolation).
- **Global duplicate scope.** Duplicate detection ignores the RIC id.
- **A failed update to a duplicate** leaves the policy's earlier fingerprint in place while marking it `REJECTED`.
- **Volatile.** All state is process memory.
- Open items about the A1 side live in A1 Related (`OI-5-a1-ric-inventory`, `OI-1-a1-ml`); the compose isolation (`a1_mock_net`) is unverified end to end because the full stack has not been run (`OI-2-compose-e2e`). The route table in R1 Termination does not include it.

## 3. Unit tests

### 3.1 Running them

```bash
cd smo/mock-near-rt-ric && PYTHONPATH=.:../shared python -m pytest tests/ -q
```

### 3.2 What is covered

| Test file | Covers | Count |
|---|---|---|
| `tests/test_main.py` | Create (enforced, empty rejected), status after create, unknown-id status, delete then query, delete idempotent, update (enforced, empty rejected, unknown id), independent tracking, duplicate content (same type rejected, other type allowed, different content allowed, update colliding with another policy, update to own content, delete frees the fingerprint) | 16 |

An autouse fixture clears `_policies` and `_fingerprints` between tests.

### 3.3 What is not covered here

- The A1 Related <-> mock round trip and the compose network isolation: `tests_integration/` (round trip); the isolation is not verified anywhere (`OI-2-compose-e2e`).
- A1AP conformance: not implemented.

## 4. References

- Call flows: [05 A1 EI registration to consumption](../docs/call-flows/05-a1-ei-registration-to-consumption.md) (the A1 side)
- OpenAPI: [`../docs/openapi/mock-near-rt-ric.json`](../docs/openapi/mock-near-rt-ric.json)
- Architecture: [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) (the southbound mocks are not R1-facing); open work: [`../OPEN_ITEMS.md`](../OPEN_ITEMS.md)
- Related READMEs: [A1 Related](../a1-related/README.md)
