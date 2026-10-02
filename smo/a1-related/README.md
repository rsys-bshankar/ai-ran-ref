# A1 Related (`a1-related/`)

> The SMO-side A1 policy mapping store and A1 termination client: it creates, updates and deletes A1 policies on a Near-RT RIC, mirrors their enforcement status, supervises policy-owning services, and wraps A1 enrichment-information (EI) type registration onto DME.

| | |
|---|---|
| Standards basis | O-RAN A1-P (policy) and A1-EI |
| R1 route / port | `/a1-related` via R1 Termination (container :8000) |
| Depends on (over R1) | DME (`POST /dme/production-capabilities`, EI registration only); southbound, not R1: the Near-RT RIC A1-P endpoint (`mock-near-rt-ric` in this build) |
| Called by | SO SMOS (`POST /a1-related/policies`), GUI / GUI BFF (policy, subscription, EI-type and service routes), policy-status subscribers (as webhook receivers) |
| Database tables | `a1_policy`, `policy_status_subscription`, `a1_ei_type`, `a1_service_registration` |
| Unit tests | 45 passed (`tests/`, SQLite, standalone) |
| Status | Done for the mapping-store scope against a test double. Open: `OI-5-a1-scope` (OWN/OTHERS scope), `OI-5-a1-ric-inventory` (types and RIC inventory), `OI-1-a1-ml` (A1-ML out of scope) |
| Time-driven behaviour | On request, never on a timer: a stale service is swept when `GET /services` reads it |

## 1. High-level design (HLD)

### 1.1 Purpose and scope

A1 policy is a separate concept from intents and is not part of the Intent Service. A1 Related is the thin R1-facing envelope over A1-P: a caller (an rApp or SO SMOS) asks for a policy; A1 Related generates the R1-facing `policyId`, calls the Near-RT RIC through its A1 termination client, and stores the `policyId` <-> Near-RT RIC mapping together with the resulting enforcement status. The policy object itself is opaque (A1TD-owned) and never interpreted.

It provides:

- policy create / read / update / delete and a filtered list;
- `enforcementStatus` as a local mirror, refreshed from the Near-RT RIC on update and on `GET .../status`;
- policy-status subscriptions with webhook notification on a status change;
- an A1-P-style service registry with keep-alive supervision (a stale service is deregistered and its policies removed);
- EI type registration (`RegisterEIType`) as a wrapper over DME's producer registration.

### 1.2 Standards basis

| Spec | What is realised | What is deliberately not |
|---|---|---|
| O-RAN A1-P (policy management, A1 policy management service API `pms-api-v3`) | Policy lifecycle, `GET /policy-types/{id}`, policy status, service registry and supervision (`/services`, keep-alive), policy-status subscriptions | The A1 transport (TLS + mTLS + OAuth2.0 + JWT per A1TP) is noted, not implemented: the client is plain HTTP against a test double. No A1TD schema validation: `policySchema` is the placeholder `{"type": "object"}`. No RIC inventory (`GET /rics`); policy types are the hardcoded `KNOWN_POLICY_TYPES` (`OI-5-a1-ric-inventory`) |
| O-RAN A1-EI | EI type registration as a bookkeeping entry (`A1EIType`) wrapping a DME type; R1AP clause 9 has no distinct EI-registration call, so it is a DME `RegisterDMEType` plus a record ([call flow 05](../docs/call-flows/05-a1-ei-registration-to-consumption.md)) | EI job management against a RIC; no A1-EI southbound |
| O-RAN A1-ML | Not implemented. R1AP clause 9 contains only 9.1 policy management; the five A1-ML operations would need genuine A1AP behaviour and are out of scope (`OI-1-a1-ml`); no schema is modelled | n/a |

There is no A1 spec file under [`../../specs/`](../../specs/README.md) to audit against ([specs README](../../specs/README.md) records this); fidelity is checked against the O-RAN-SC reference's `pms-api-v3` shape.

### 1.3 Position in the platform

```
 SO SMOS / GUI --R1--> A1 Related --A1-P (HTTP, not R1)--> Near-RT RIC  (mock-near-rt-ric, isolated net)
                          |
                          +--R1--> DME   (EI type registration only)
                          +--webhook--> policy-status subscribers
```

- The Near-RT RIC test double sits on an isolated `a1_mock_net` compose network with no published ports; A1 Related is the only module on both networks. It is not reachable through R1.
- DME is called only for EI registration. DME and RAN NF OAM never call A1 Related. A1, Near-RT RIC and xApps are not on the DME loop: inference runs inside the rApp.
- R1 Termination's routing-table comment marks `/a1-related` as reserved, inert until a Near-RT RIC is attached; the route is nevertheless used by SO SMOS and the GUI against the mock.

### 1.4 Ownership

| Owns | Does not own → owner |
|---|---|
| `policyId` <-> Near-RT RIC policy id mapping and the policy mirror | The policy content and its semantics → rApp / A1TD |
| Enforcement-status mirror and its change notification | Actual enforcement → the Near-RT RIC |
| Service registry and keep-alive supervision | Intent handling (TS 28.312) → Intent Service |
| EI type bookkeeping (`A1EIType`) | The DME type, producer and data jobs behind an EI type → DME |
| A1 termination client (southbound trust boundary) | R1 exposure, token introspection → R1 Termination, SME |

### 1.5 Design decisions

- **Mapping store, not policy engine.** `policy_object` is stored verbatim; `policy_id` (R1-facing) differs from `near_rt_ric_policy_id` (the RIC's own id). All update / delete / status calls use the RIC's id.
- **Status is a mirror, never fabricated.** `enforcementStatus` comes from the RIC response (`ENFORCED`, `REJECTED`, `PENDING`, `SUSPENDED`); `GET /policies/{id}/status` always refreshes from a live RIC call rather than trusting the cache.
- **Separate trust boundary.** The A1 termination client (`a1_termination_client.py`) is distinct from `R1Client`: `R1Client` goes through R1 Termination to SMO modules, the A1 client goes out of the SMO to a Near-RT RIC.
- **Failure behaviour.** A RIC rejection (for example an empty `policyObject`) is a normal outcome: `201` / `200` with `enforcementStatus: REJECTED` and a stored `rejectionReason`. A transport failure or a non-2xx from the RIC is not caught: `create`, `update` and `status` propagate it (HTTP 500).
- **Best-effort notifications.** Status-change delivery uses `smo_shared.webhook` with a 2 s timeout; an unreachable subscriber never fails the triggering call.
- **No scheduler.** Service supervision is lazy: a stale service is swept when `GET /services` next reads it.
- **Idempotent deletes** for policies, subscriptions and EI types; strict 404 for services.
- **AuthZ elided.** Policy creation does not authorize the creator; `creatorId` is the rApp / service identity. Unregistered services may create policies, as in the reference. OWN / OTHERS subscription scope has no subscriber identity to compare and is treated as ALL (`OI-5-a1-scope`).

## 2. Low-level design (LLD)

### 2.1 Code map

| File | Responsibility |
|---|---|
| `app/main.py` | All routes, `KNOWN_POLICY_TYPES`, status-change notification, service sweep, EI registration, DME callback stubs |
| `app/a1_termination_client.py` | Southbound HTTP client to the Near-RT RIC (`/a1-p/policies...`), 5 s timeout |
| `app/models.py` | SQLAlchemy models |

### 2.2 Data model

**`a1_policy`** (PK `policy_id`, UUID)

| Column | Notes |
|---|---|
| `near_rt_ric_policy_id` | The RIC's own id (string); used for every southbound call |
| `policy_type_id` | must be in `KNOWN_POLICY_TYPES` at create |
| `creator_id` | the creating rApp / service; also the key for service-owned policies |
| `near_rt_ric_id` | target RIC |
| `policy_object` | JSON, opaque |
| `enforcement_status` | `PENDING` / `ENFORCED` / `REJECTED` / `SUSPENDED` (local mirror), CHECK-constrained in Postgres |
| `rejection_reason` | nullable |

**`policy_status_subscription`** (PK `subscription_id`): `notification_destination`, `subscription_scope` (`OWN` / `OTHERS` / `ALL`), `policy_id_list`, `policy_type_id_list`, `near_rt_ric_id_list` (lists nullable; unset matches everything). CHECK: `subscription_scope` and `policy_id_list` are mutually exclusive.

**`a1_ei_type`** (PK `ei_type_id`, string): `registered_by`, `ei_source_dme_type_id` (bare UUID of the DME registration; cross-module, no FK).

**`a1_service_registration`** (PK `service_id`, caller-supplied): `callback_url`, `keep_alive_interval_seconds` (0 = supervision disabled), `last_activity_at`.

### 2.3 State machines

None: stateless with respect to a lifecycle FSM. `enforcement_status` is a mirror of the RIC's answer with no guards and no forbidden transitions; any value can follow any other. The only derived behaviour is that a change of value on update or status refresh triggers subscriber notification.

### 2.4 API

All routes are under `/a1-related` through R1. Lists return `{items, total, limit, offset}` except `/policy-types` (fixed enum, a bare list) and `/services` (spec-fixed `{serviceList}`).

**Policy types**

| Method | Path | Purpose / notable errors |
|---|---|---|
| GET | `/policy-types` | The hardcoded catalog (`ORAN_QoSandTSP_6.0.1`, `ORAN_TrafficSteeringPreference_6.0.1`); optional `near_rt_ric_id` echoed back (default `mock-near-rt-ric-001`) |
| GET | `/policy-types/{policy_type_id}` | `{policySchema: {"type": "object"}, statusSchema: null}`; 404 `POLICY_TYPE_NOT_FOUND` |

**Policies**

| Method | Path | Purpose / notable errors |
|---|---|---|
| POST | `/policies` | Body `policyTypeId`, `policyObject`, `nearRtRicId`, `creatorId`. 201 `{policyId, enforcementStatus}`. 422 `POLICY_TYPE_NOT_SUPPORTED` |
| GET | `/policies` | List; filters `policy_type_id`, `near_rt_ric_id`, `creator_id` |
| GET | `/policies/{policy_id}` | Read. Unknown id: unhandled 500 (see limits) |
| PUT | `/policies/{policy_id}` | Body is the new policy object itself. Calls the RIC, stores the new status, notifies on a status change |
| DELETE | `/policies/{policy_id}` | 204; deletes on the RIC then locally; unknown id is a no-op |
| GET | `/policies/{policy_id}/status` | Refreshes from the RIC, notifies on change; returns `{policyId, enforcementStatus}` |

**Policy-status subscriptions** (`/policies/subscriptions` is declared before `/policies/{policy_id}` so the UUID route does not capture it)

| Method | Path | Purpose / notable errors |
|---|---|---|
| POST | `/policies/subscriptions` | Body `notificationDestination`, `subscriptionScope?`, `policyIdList?`, `policyTypeIdList?`, `nearRtRicIdList?`. 201 `{subscriptionId}`. 422 `SUBSCRIPTION_SCOPE_CONFLICT` if scope and `policyIdList` are both given |
| GET | `/policies/subscriptions` | List |
| DELETE | `/policies/subscriptions/{subscription_id}` | 204, idempotent |

**Service registry and supervision**

| Method | Path | Purpose / notable errors |
|---|---|---|
| PUT | `/services` | Register or update in place; body `serviceId`, `callbackUrl?`, `keepAliveIntervalSeconds` (default 0); resets the activity clock; returns `{}` |
| GET | `/services` | All (`{serviceList}`) or one with `service_id` (404 `A1_SERVICE_REGISTRATION_NOT_FOUND`); sweeps stale services on the way |
| PUT | `/services/{service_id}/keepalive` | Resets the clock; 404 if unknown |
| DELETE | `/services/{service_id}` | Deregisters and deletes the service's policies (RIC-side delete each); 404 if unknown |

**EI types**

| Method | Path | Purpose |
|---|---|---|
| POST | `/ei-types/register` | Query parameters `ei_type_id`, `registered_by`, `dme_namespace`, `dme_name`, `dme_version`. Registers a DME producer capability, records `A1EIType`; returns `{eiTypeId, eiSourceDmeTypeId}` |
| GET | `/ei-types` | List |
| DELETE | `/ei-types/{ei_type_id}` | 204, idempotent; local record only (the DME registration is not removed) |

**Liveness and DME callbacks**: `GET /health` (also the `producerHealthCallbackUrl` registered with DME), `POST /dme-jobs` (acks only), `DELETE /dme-jobs/{data_job_id}` (204, no-op).

### 2.5 Interactions

**Southbound A1-P (`A1TerminationClient`, base URL `MOCK_NEAR_RT_RIC_URL`, 5 s timeout)**

| Operation | Call | Failure behaviour |
|---|---|---|
| create | `POST /a1-p/policies?near_rt_ric_id=&policy_type_id=` with the policy object as body | `raise_for_status`; the exception is unhandled (500) |
| update | `PUT /a1-p/policies/{ric_policy_id}` | same |
| delete | `DELETE /a1-p/policies/{ric_policy_id}` | status ignored; a connection error propagates |
| status | `GET /a1-p/policies/{ric_policy_id}/status` | `raise_for_status` |

**Status-change notification.** After `PUT /policies/{id}` or `GET .../status`, if the status changed, every subscription whose filters match (an unset list matches all; `policyIdList`, `policyTypeIdList`, `nearRtRicIdList`) receives `POST notificationDestination` with `{policyId, policyTypeId, nearRtRicId, enforcementStatus}` via `smo_shared.webhook.post_webhook` (2 s timeout, failures swallowed). Creation does not notify.

**Service supervision.** A service with `keep_alive_interval_seconds > 0` whose last activity is older than that interval is deregistered, and each of its policies (matched on `creator_id`) is deleted southbound and locally, when `GET /services` reads it.

**EI registration.** `POST /dme/production-capabilities` over R1 with `typeName = <namespace>.<name>`, `producerId = registered_by`, `dataProductionSchema {}`, and callbacks `http://a1-related:8000/health` and `/dme-jobs`; the DME `registrationId` is stored as `ei_source_dme_type_id`. A DME failure is not caught.

### 2.6 Configuration

| Variable | Default | Meaning |
|---|---|---|
| `MOCK_NEAR_RT_RIC_URL` | `http://mock-near-rt-ric:8000` | Near-RT RIC A1-P base URL |
| `SMO_DATABASE_URL` | `postgresql+psycopg://smo:smo@postgres:5432/smo` | Database (shared lib) |
| `R1_GATEWAY_URL` | `http://r1-termination:8000` | R1 gateway for the DME call (shared lib) |

### 2.7 Error codes

Returned as `{"detail": {"type": "about:blank", "title": <code>, "status", "detail"}}` (code = `title`), via `framework_error`.

| Code | HTTP | When |
|---|---|---|
| `POLICY_TYPE_NOT_SUPPORTED` | 422 | `POST /policies` with a type outside `KNOWN_POLICY_TYPES` |
| `POLICY_TYPE_NOT_FOUND` | 404 | `GET /policy-types/{id}` for an unknown type |
| `SUBSCRIPTION_SCOPE_CONFLICT` | 422 | A subscription with both `subscriptionScope` and `policyIdList` |
| `A1_SERVICE_REGISTRATION_NOT_FOUND` | 404 | Keep-alive, delete, or `GET /services?service_id=` for an unknown service |

### 2.8 Limits and open items

- **Test double only.** The RIC is `mock-near-rt-ric`; no A1TP transport security, no A1TD schema validation, `policySchema` is a placeholder.
- **Types and RICs.** `KNOWN_POLICY_TYPES` is hardcoded and no RIC inventory exists (`OI-5-a1-ric-inventory`).
- **Subscription scope.** OWN / OTHERS is treated as ALL (`OI-5-a1-scope`).
- **A1-ML** is out of scope (`OI-1-a1-ml`).
- **Unguarded paths.** `GET`/`PUT /policies/{id}` and `GET .../status` for an unknown id, and any RIC transport failure, end in an unhandled 500 rather than a ProblemDetails (the first is asserted by a test).
- **Error table.** `smo_shared/errors.py` says A1 policy management keeps its own error table; in the code A1 Related uses the shared `framework_error` codes above.
- **No scheduler** (lazy supervision), **no EI job management**, `/dme-jobs` acks only.

## 3. Unit tests

### 3.1 Running them

```bash
cd smo/a1-related && PYTHONPATH=.:../shared python -m pytest tests/ -q
```

### 3.2 What is covered

| Test file | Covers | Count |
|---|---|---|
| `tests/test_main.py` | Policy types and definitions; create (unknown type, enforced, empty object rejected by RIC); status refresh; subscription scope conflict; read / update / delete (idempotent, southbound delete called); unknown-id 500; subscription CRUD and webhook delivery (match, unchanged status, filtered out, unreachable subscriber, status-refresh trigger); policy list filters; service registry (register, update in place, list, 404s, keep-alive, delete with policy teardown, stale auto-deregistration, supervision disabled); EI type register / list / deregister; `/health` and `/dme-jobs` callbacks; subscription list route ordering | 45 |

The southbound client is replaced by a fake via FastAPI dependency override; `R1Client.post` is monkeypatched for EI registration; subscriber callbacks are intercepted by patching `httpx.post`.

### 3.3 What is not covered here

- Real HTTP round trip to `mock-near-rt-ric` (policy create -> enforced -> status), the SO SMOS dispatch, EI registration against a real DME: `tests_integration/`.
- RIC transport failures (the 500 path), A1TP security, A1-ML: not covered / not implemented.

## 4. References

- Call flows: [05 A1 EI registration to consumption](../docs/call-flows/05-a1-ei-registration-to-consumption.md), [14 correlation id](../docs/call-flows/14-correlation-id-propagation.md)
- OpenAPI: [`../docs/openapi/a1-related.json`](../docs/openapi/a1-related.json)
- Architecture and R1 conventions: [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md); open work: [`../OPEN_ITEMS.md`](../OPEN_ITEMS.md)
- Specs: [`../../specs/README.md`](../../specs/README.md) (no A1 spec file is bundled)
- Related READMEs: [mock-near-rt-ric](../mock-near-rt-ric/README.md), [DME](../dme/README.md)
