# Service Orchestration SMOS (`so-smos/`)

> Runs a multi-step service order (config, deploy, infra, AI/ML job steps) sequentially against the owning modules, stopping at the first failure.

| | |
|---|---|
| Standards basis | O-RAN SMO-ARCH §4.2.7 SMOS (role and capabilities); interfaces unspecified, so internal logic |
| R1 route / port | `/so-smos` via R1 Termination (container :8000) |
| Depends on (over R1) | RAN NF OAM, NFO, FOCOM, AIMgF (one per dispatch-table entry) |
| Called by | GUI BFF (operators submit and cancel orders); SA SMOS reads orders (`GET /so-smos/orders/{id}`) to resolve a monitor's deployment; any R1 consumer with the route |
| Database tables | `service_order` |
| Unit tests | 23 passed (`tests/`, SQLite, standalone) |
| Status | Done for Phase 1: no compensation, no asynchronous execution (see 2.8) |

## 1. High-level design (HLD)

### 1.1 Purpose and scope

An operator or rApp composes a list of steps, each naming a step type and the module that performs it. SO SMOS
executes them in order through a fixed dispatch table, records the per-step result on the order and returns it. It
orchestrates; it does not perform any step itself and holds no domain state beyond the order record.

### 1.2 Standards basis

SO SMOS is the "Service and Slice Subnet Orchestration SMOS" of O-RAN WG1 SMO-ARCH §4.2.7. That clause states capabilities only (NOTE 2: SMOS interfaces and modelling are not specified), so the dispatch table and `service_order` model are internal design; no 3GPP or O-RAN IOC is realised. The `rmihRegistration` field on an order records the
identity under which SO SMOS would act as an intent handling function (an RMIH, which SMO-ARCH §4.7 lists SO SMOS as able to be) (default `so-smos`), but this module does
not register as an RMIH with Intent Service and does not use it (see 2.8).

### 1.3 Position in the platform

```
 operator / GUI ──POST /orders──► SO SMOS ──► RAN NF OAM | NFO | FOCOM | AIMgF    (one call per step)
 SA SMOS ──GET /orders/{id}──► SO SMOS
```

It never calls DME, MDAF, Intent Service or MLMR, and never reads another module's tables.

### 1.4 Ownership

| Owns | Does not own → owner |
|---|---|
| `ServiceOrder` record: scope, per-step status and result, RMIH registration label | Config jobs and schema pre-check → RAN NF OAM |
| The dispatch table (step type × target module → one R1 call) and fail-fast execution | NF deployments and heal → NFO |
| | Infrastructure provisioning → FOCOM |
| | Training, validation, emulation, model runtime deploy, inference → AIMgF |
| | Monitoring and remediation of an order → SA SMOS |

### 1.5 Design decisions

- **Sequential and fail-fast.** The first `FAILED` step halts the order; every later step is recorded `PENDING`
  and never attempted.
- **No rollback.** Completed steps are not compensated (no compensating-transaction mechanism in Phase 1). For
  example, infrastructure provisioned by an earlier step is not deprovisioned when a later step fails.
- **An error response is a failure.** A downstream module that answers 4xx/5xx raises `DownstreamError`, so the
  step is `FAILED` with the status and body in `error`. Earlier versions recorded such a step `COMPLETED`, which
  defeated fail-fast.
- **Any exception halts the order.** Transport errors and unexpected failures are recorded as `FAILED`, never
  raised to the caller of `POST /orders`.
- **Unknown (stepType, targetModule) fails the step** with `no dispatcher for (...)` and halts the order; it does
  not reject the request.
- **Model-runtime deploy is distinct from workload deploy.** `("DEPLOY", "AIMGF")` deploys a certified model's
  serving runtime, `("DEPLOY", "NFO")` deploys a workload. The AIMgF guard (`CERTIFIED` or `PROMOTED`) fires inside
  AIMgF and surfaces here as an ordinary `DownstreamError`.
- **Execution is synchronous.** `POST /orders` runs every step before it returns, although it answers 202.
- **Security.** No per-service auth; bearer protection is declared in the OpenAPI contract and enforced by R1
  Termination. The GUI BFF allows submit and cancel for operators. Steps default `requestedBy` / `producerId` /
  `creatorId` to `so-smos`.

## 2. Low-level design (LLD)

### 2.1 Code map

| File | Responsibility |
|---|---|
| `app/main.py` | App, `/health`, order submit / read / cancel / list |
| `app/dispatch.py` | `DISPATCH_TABLE`, one dispatcher per entry, `DownstreamError` / `_ensure_ok`, `execute_order` |
| `app/models.py` | `ServiceOrder` |

### 2.2 Data model

`service_order`

| Column | Notes |
|---|---|
| `order_id` | UUID PK |
| `scope` | String, required, free text |
| `steps` | JSON list; replaced wholesale with the executed steps (plain JSON columns do not track in-place mutation) |
| `homing_decision` | JSON, nullable; no route sets it, it is returned as stored |
| `rmih_registration` | String, default `so-smos` |

Each step is the caller's dict plus `status` (`COMPLETED` / `FAILED` / `PENDING` / `CANCELLED`) and `result` or
`error`.

### 2.3 State machines

Per step, within one order

| From | Event | To |
|---|---|---|
| (submitted) | executed in order, dispatcher succeeds | `COMPLETED` (with `result`) |
| (submitted) | dispatcher raises, error response, or no dispatcher | `FAILED` (with `error`); order halts |
| (submitted) | an earlier step `FAILED` | `PENDING` |
| `PENDING` | `POST /orders/{id}/cancel` | `CANCELLED` |

`COMPLETED`, `FAILED` and already `CANCELLED` steps are never changed by cancel. There is no resume: a `PENDING`
step is never executed after the submit call returns.

### 2.4 API

| Method | Path | Purpose | Notable errors |
|---|---|---|---|
| POST | `/orders` | Submit and run an order `{scope, steps[], rmihRegistration?}`. Returns `{orderId, steps}` (202). | Step failures are in the body, not an HTTP error |
| GET | `/orders` | List (paging); each item has `orderId`, `scope`, `steps`, `homingDecision`, `rmihRegistration` | |
| GET | `/orders/{order_id}` | Read `{orderId, steps, homingDecision}` | Unknown id is an unhandled 500 |
| POST | `/orders/{order_id}/cancel` | Mark `PENDING` steps `CANCELLED` | Unknown id is an unhandled 500 |
| GET | `/health` | Liveness probe | |

Dispatch table (each dispatcher returns the downstream JSON as the step `result`)

| stepType | targetModule | Call | Step fields used |
|---|---|---|---|
| `CONFIG` | `RAN_NF_OAM` | `POST /ran-nf-oam/config-jobs` | `scope`, `changes`, `requestedBy?`, `msacRole?` |
| `DEPLOY` | `NFO` | `POST /nfo/deployments` | `nfDeploymentDescriptorId`, `name?` (default `deploy-<id>`), `requiredResourceTypeId?` |
| `INFRA` | `FOCOM` | `POST /focom/resources/provision` | `spec` |
| `TRAINING` | `AI_ML_WORKFLOW` | `POST /aimgf/training-jobs` | `modelId` or `modelCoordinationGroupId`, `producerId?`, `requiredData?`, `validationCriteria?` |
| `VALIDATION` | `AI_ML_WORKFLOW` | `POST /aimgf/validation-jobs` | `modelId`, `trainingJobId`, `producerId?`, `validationCriteria?` |
| `EMULATION` | `AI_ML_WORKFLOW` | `POST /aimgf/emulation-jobs` | `modelId`, `producerId?`, `emulationCriteria?` |
| `DEPLOY` | `AIMGF` | `POST /aimgf/models/{modelId}/runtime/deploy` | `modelId` |
| `INFERENCE` | `AI_ML_WORKFLOW` | `POST /aimgf/models/{modelId}/inference-jobs` (query `notification_destination` when given) | `modelId`, `notificationDestination?` |

### 2.5 Interactions

One synchronous R1 call per executed step (table above), no callbacks and no background tasks. A missing required
step field (for example `nfDeploymentDescriptorId`) raises inside the dispatcher and fails that step. A downstream
2xx is `COMPLETED`; anything else fails the step and halts the order. The order row is committed once, after all
steps have run, so an order is not visible while it executes.

### 2.6 Configuration

| Variable | Default | Use |
|---|---|---|
| `SMO_DATABASE_URL` | none; required (the service refuses to start without it) | Database |
| `R1_GATEWAY_URL` | `http://r1-termination:8000` | Outbound R1 base URL |
| `SMO_INVOKER_ID`, `SMO_INVOKER_SECRET` | unset (self-onboards at SME) | Outbound OAuth2 identity |

No SO-SMOS-specific variables.

### 2.7 Error codes

No module-specific ProblemDetails. Per-step failures are reported inside the response body as
`status: "FAILED"` with `error` set to `no dispatcher for (<stepType>, <targetModule>)`, `<status>: <body>` of the
downstream error response, or the exception text. HTTP-level errors are FastAPI validation (`HTTPValidationError`,
422) for a malformed body (missing `scope` or `steps`), and unhandled 500 for an unknown order id on read or cancel.

### 2.8 Limits and open items

- No compensation of completed steps when a later step fails.
- Synchronous execution inside the request; a long order holds the HTTP call open, and `PENDING` steps are never
  resumed.
- `homing_decision` is never written and `rmihRegistration` is stored but unused: SO SMOS does not register with
  Intent Service.
- Unknown order ids on read and cancel return 500 instead of 404.
- Test depth: `../OPEN_ITEMS.md` OI-4 (counts `def test_` functions, 15; the 23 reported here include parametrized
  cases).

## 3. Unit tests

### 3.1 Running them

```bash
cd smo/so-smos && PYTHONPATH=.:../shared python -m pytest tests/ -q
```

### 3.2 What is covered

| File | Covers | Tests |
|---|---|---|
| `tests/test_dispatch.py` | `execute_order` (all succeed, first failure halts and leaves `PENDING`, unknown pair), `_ensure_ok` (pass-through, 4xx raises), every dispatcher posts to its own module path (parametrized), inference notification destination as query param (present and absent), full AI/ML pipeline composed in one order, model-runtime deploy distinct from workload deploy | 18 |
| `tests/test_main.py` | Submit persists and returns executed steps, read, cancel marks only `PENDING` steps, list, `/health` | 5 |

### 3.3 What is not covered here

Real downstream modules (the R1 client is stubbed). Cross-service behaviour, such as an order feeding an SA SMOS
`RECONNECT` heal, is covered in `tests_integration/` (in-process mesh). Unknown-order handling is untested.

## 4. References

- Call flows: [04 closed-loop assurance](../docs/call-flows/04-closed-loop-assurance.md) (CONFIG then DEPLOY), [10 SO SMOS multi-step order](../docs/call-flows/10-so-smos-multi-step-infra-training-deploy.md), [02 AI/ML model train to inference](../docs/call-flows/02-aiml-model-train-to-inference.md)
- OpenAPI: [`../docs/openapi/so-smos.json`](../docs/openapi/so-smos.json)
- Related: [`../sa-smos/README.md`](../sa-smos/README.md), [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md), [`../OPEN_ITEMS.md`](../OPEN_ITEMS.md)
