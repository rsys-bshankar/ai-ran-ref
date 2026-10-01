# Service Assurance SMOS (`sa-smos/`)

> Assurance monitors with threshold evaluation and remedial actions, plus the generic O1-CM intent handler that enacts Intent Service intents as O1 configuration changes.

| | |
|---|---|
| Standards basis | O-RAN SMO-ARCH §4.2.8 SMOS (role and capabilities); interfaces unspecified, so monitors and remedial actions are internal logic; its O1-CM handler acts as a 3GPP TS 28.312 intent handling function (RMIH) |
| R1 route / port | `/sa-smos` via R1 Termination (container :8000) |
| Depends on (over R1) | Intent Service; DME (`/dme/actions`); RAN NF OAM (`/ran-nf-oam/config-jobs`); NFO (`/nfo/deployments/{id}/heal`); SO SMOS (`/so-smos/orders/{id}`); rApp Management (`/rapp-mgmt/instances/...`); AIMgF (`/aimgf/training-jobs`) |
| Called by | GUI BFF (monitors, evaluate, escalate, remedial actions); Intent Service (new-intent push to the O1-CM handler); rApp demos and integration environments (O1-CM registration) |
| Database tables | `assurance_monitor`, `remedial_action`, `o1_cm_enactment` |
| Unit tests | 32 passed (`tests/`, SQLite, standalone) |
| Status | Done for Phase 1; `SCALE` always escalates and `CONFIG_CHANGE` is a stub write (see 2.8) |

## 1. High-level design (HLD)

### 1.1 Purpose and scope

SA SMOS has two independent roles.

1. **Assurance.** An `AssuranceMonitor` holds requirement thresholds for exactly one kind of target: a service
   order (NF deployment), an AIMgF model coordination group, or an rApp instance. A caller evaluates metrics
   against it and, on a breach, asks for a remedial action. The action is dispatched to the module that can
   perform it, and the outcome (`RESOLVED` or `ESCALATED`) is recorded. `ESCALATED` rows are the operator queue.
2. **O1-CM intent handling.** SA SMOS registers as RMIH `sa-smos` with Intent Service for `RAN_SUBNETWORK`
   expectations whose targets are `<IOC>.<attribute>` CM attributes. It enacts a pushed intent by writing the
   change through DME's O1 action mediation and reports fulfilment back to Intent Service.

It does not own intents (Intent Service), configuration jobs (RAN NF OAM), O1 action mediation (DME), workloads
(NFO) or rApp versions (rApp Management).

### 1.2 Standards basis

SA SMOS is the "Service and Slice Subnet Assurance SMOS" of O-RAN WG1 SMO-ARCH §4.2.8. That clause states capabilities only (NOTE 2: SMOS interfaces and modelling are not specified), so the assurance part is internal design. The O1-CM handler is a TS 28.312 intent handling function: it registers an
`IntentHandlingFunction` with an `IntentHandlingCapability` (object type `RAN_SUBNETWORK`, target infos for each CM
target with condition `IS_EQUAL_TO`), receives intents, and publishes `IntentFulfilmentReport`s. See
[`../intent-service/README.md`](../intent-service/README.md) and
[`TS28312_IntentNrm.yaml`](../../specs/5G_APIs/TS28312_IntentNrm.yaml). The CM attribute names are TS 28.541 class
and attribute names. It realises no TS 28.312 expectation semantics beyond `IS_EQUAL_TO` target setting.

### 1.3 Position in the platform

```
 operator / GUI ──► SA SMOS ──heal──► NFO
                      │  ├──rollback──► rApp Management
                      │  ├──retrain──► AIMgF
                      │  └──config job──► RAN NF OAM (stub write)
                      │
 Intent Service ──push intentId──► SA SMOS O1-CM handler ──GET /intents/{id}──► Intent Service
                                        ├──POST /dme/actions──► DME ──► RAN NF OAM config job ──► O1
                                        └──POST /intent-reports──► Intent Service
```

### 1.4 Ownership

| Owns | Does not own → owner |
|---|---|
| `AssuranceMonitor` (thresholds and one target) | The service order a monitor targets → SO SMOS |
| `RemedialAction` records and their `RESOLVED` / `ESCALATED` outcome | Heal / deployment state → NFO |
| O1-CM RMIH registration (`sa-smos`) and `O1CmEnactment` records | Intent, intent reports, RMIH registry → Intent Service |
| | O1 change mediation, action ids, replay → DME; config jobs and schema pre-check → RAN NF OAM |
| | rApp version history and rollback → rApp Management |
| | Retraining → AIMgF |

### 1.5 Design decisions

**Assurance**

- **At most one target per monitor.** Enforced in the handler and by the database `one_target_only` CHECK
  constraint as backstop. A monitor with no target is allowed (thresholds only).
- **Dispatch is chosen by target, then by action type.** A coordination-group monitor ignores `actionType` and
  always triggers a retrain of the group via AIMgF, because NF-oriented actions have no meaning for a model group.
- **ROLLBACK needs a version history.** Only rApp Management keeps one, so ROLLBACK is dispatched only for an
  rApp-instance-scoped monitor; any other monitor is refused (409 `ROLLBACK_HISTORY_UNAVAILABLE`) rather than
  pretending.
- **A downstream refusal is an outcome, not an error.** If the target module answers non-2xx, the action is
  recorded `ESCALATED` (with the refusal reason in `detail` for rollback) and the call returns 201.
- **RECONNECT means heal.** The NFO deployment is resolved from the order's completed `DEPLOY` step
  (`result.nfDeploymentId`), or from rApp Management's `workloadRef` for an rApp-scoped monitor (which follows an
  upgrade to the replacement instance). If none can be resolved, the action is `ESCALATED`.
- **Auto-execution gating.** `requester_is_admin` is stored as `auto_executed`; the GUI BFF sets it from the
  caller's role.

**O1-CM handler**

- **Intent Service is the source of truth.** The push carries only `intentId`; the handler reads the intent back.
- **One DME action per expectation, replay-safe.** The action id is `uuid5(namespace, "{intentId}:{expectationId}")`,
  so a re-pushed intent replays the same action, which DME ignores (status `IGNORED`, `originalStatus` kept)
  instead of writing the change twice.
- **Per-cell fan-out.** The expectation's `objectInstance` is the managed element; each `Cell` object context
  value gives a change with `managedFunctionRef = <IOC>=<cell>`; with no cell context a single change without a
  function ref is written.
- **Unsupported targets are reported, not written.** A target is unsupported when its name is not a registered CM
  target, its condition is not `IS_EQUAL_TO`, its value is not in the target's allowed list (when one exists), or
  the expectation has no `objectInstance`. Unsupported targets degrade the report; the rest still enact.
- **Registration is explicit and idempotent.** Nothing registers at startup. `POST /o1-cm-handler/registration`
  deletes any existing `sa-smos` RMIH and re-registers; the demos and integration environments call it. The
  capability list held by Intent Service is the source of truth for the CM targets, falling back to the defaults
  if the lookup fails.
- **Failure behaviour.** DEACTIVATED intents are skipped (`status: SKIPPED`, no report). An intent addressed to a
  different RMIH is refused. A failed DME action, or one whose status is not `COMPLETED`, yields a `NOT_FULFILLED`
  / `DEGRADED` report with the action status in `notFulfilledReasons`.
- **Security.** No per-service auth; bearer protection is declared in the OpenAPI contract and enforced by R1
  Termination. DME actions are attributed to `requestedBy = sa-smos:o1-cm-intent-handler`. The GUI BFF allows the
  assurance routes for operators.

## 2. Low-level design (LLD)

### 2.1 Code map

| File | Responsibility |
|---|---|
| `app/main.py` | App, `/health`, monitors, threshold evaluation, remedial-action dispatch, escalation, list/read routes; mounts the O1-CM router last |
| `app/o1cm.py` | O1-CM handler: registration, default CM targets, `_changes_for` / `_cells`, `enact_intent`, enactment list |
| `app/models.py` | `AssuranceMonitor`, `RemedialAction`, `O1CmEnactment` |

### 2.2 Data model

`assurance_monitor`

| Column | Notes |
|---|---|
| `monitor_id` | UUID PK |
| `target_order_id` | Bare UUID, SO SMOS order |
| `target_coordination_group_id` | Bare UUID, AIMgF model coordination group |
| `target_rapp_instance_id` | Bare UUID, rApp Management instance (may be superseded by an upgrade; rApp Management resolves it) |
| `analytics_subscription_id` | Bare UUID, stored reference only |
| `requirement_thresholds` | JSON `{metric: minimum}` |
| constraint | `one_target_only`: at most one of the three target columns is non-null |

`remedial_action`: `action_id` PK; `monitor_id` FK `assurance_monitor` (no cascade); `action_type`; `auto_executed`;
`auto_execution_scope_config` (unused by routes); `outcome` (`RESOLVED` / `ESCALATED`).

`o1_cm_enactment`: `enactment_id` PK; `intent_id` bare UUID (Intent Service); `status` (`FULFILLED` /
`NOT_FULFILLED`); `actions` JSON `[{expectationId, actionId, forwardedJobId, status, replayed}]`;
`unsupported_targets` JSON `[{expectationId, targetName, reason}]`; `intent_report_id` bare UUID (null if the
report could not be published); `created_at`.

### 2.3 State machines

None: stateless. A `RemedialAction` is written once with its final outcome. The enactment of an intent is a single
synchronous handler call.

### 2.4 API

Assurance

| Method | Path | Purpose | Notable errors |
|---|---|---|---|
| POST | `/monitors` | Register. Query `target_order_id`, `target_coordination_group_id`, `target_rapp_instance_id`, `analytics_subscription_id`; JSON body is the thresholds object (`{metric: minimum}`). Returns `{monitorId}` (201). | 422 `COORDINATION_GROUP_MISMATCH` when more than one target is given |
| GET | `/monitors`, `/monitors/{monitor_id}` | List (paging) / read | 404 `ASSURANCE_MONITOR_NOT_FOUND` |
| POST | `/monitors/{monitor_id}/evaluate` | Body is the current metrics object. Returns `{monitorId, breaches}`; a metric is a breach when below its threshold, and a missing metric counts as 0. | Unknown monitor is an unhandled 500 |
| POST | `/monitors/{monitor_id}/remedial-actions` | Query `action_type` (`CONFIG_CHANGE` / `SCALE` / `RECONNECT` / `ROLLBACK`), `requester_is_admin`. Returns `{actionId, outcome, result?, detail?}` (201). | 404 `ASSURANCE_MONITOR_NOT_FOUND`; 409 `ROLLBACK_HISTORY_UNAVAILABLE`; 422 `COORDINATION_GROUP_MISMATCH` for an unknown action type |
| POST | `/monitors/{monitor_id}/escalate` | Query `reason`. Records an `ESCALATED` `CONFIG_CHANGE` action; the reason is echoed, not stored. | Does not check that the monitor exists |
| GET | `/remedial-actions` | List (`monitor_id`, `outcome`, paging); `outcome=ESCALATED` is the operator queue | |
| GET | `/health` | Liveness probe | |

Dispatch by target and action type

| Monitor target | Action | Call | Outcome |
|---|---|---|---|
| Coordination group | any | `POST /aimgf/training-jobs {modelCoordinationGroupId, producerId: "sa-smos"}` | `RESOLVED` if < 300 else `ESCALATED` |
| any | `CONFIG_CHANGE` | `POST /ran-nf-oam/config-jobs {requestedBy: "sa-smos", scope: "cell", changes: []}` | `RESOLVED` if < 300 else `ESCALATED` |
| any | `SCALE` | none (NFO scaling is a Phase 1 stub) | always `ESCALATED` |
| rApp instance | `ROLLBACK` | `POST /rapp-mgmt/instances/{id}/rollback` | `RESOLVED` (`result` names the replacement instance) or `ESCALATED` with `detail` |
| not rApp instance | `ROLLBACK` | none | 409 `ROLLBACK_HISTORY_UNAVAILABLE` |
| rApp instance | `RECONNECT` | `GET /rapp-mgmt/instances/{id}/versions` for `workloadRef`, then `POST /nfo/deployments/{id}/heal` | `RESOLVED`, or `ESCALATED` if unresolved or the heal is refused |
| order or none | `RECONNECT` | `GET /so-smos/orders/{id}` for the `DEPLOY` step's `nfDeploymentId`, then heal | as above |

O1-CM intent handler

| Method | Path | Purpose | Notable errors |
|---|---|---|---|
| POST | `/o1-cm-handler/registration` | Register (replace) RMIH `sa-smos` at Intent Service. Optional body `{cmTargets: {"<IOC>.<attribute>": [allowed values]}, intentHandlingScope: ["RAN"]}`; defaults below. Returns `{rmihId, cmTargets, notificationDestination}` (201). | 422 `SCHEMA_VALIDATION_FAILED` for a target name without a `.` or when Intent Service refuses |
| DELETE | `/o1-cm-handler/registration` | Deregister (204); ends intents addressed to `sa-smos` | |
| POST | `/o1-cm-handler/intents` | Intent Service's push, body `{intentId}`. Returns the enactment view, or `{status: "SKIPPED"}` for a DEACTIVATED intent. | 404 `INTENT_NOT_FOUND`; 422 `RMIH_CAPABILITY_MISMATCH` (addressed to another RMIH) |
| GET | `/o1-cm-handler/enactments` | List (`intent_id`, paging), newest first | |

Default supported CM targets (an empty list means any value; RAN NF OAM's schema pre-check validates it)

| Target | Allowed values |
|---|---|
| `NRCellDU.administrativeState` | `LOCKED`, `UNLOCKED` |
| `CESManagementFunction.energySavingControl` | `TO_BE_ENERGY_SAVING`, `TO_BE_NOT_ENERGY_SAVING` |
| `NRCellRelation.cellIndividualOffset` | any (six QOffsetRange dB entries) |
| `CommonBeamformingFunction.digitalTilt` | any |
| `NRSectorCarrier.configuredMaxTxPower` | any |
| `NRFreqRelation.cellReselectionPriority` | any (Cell-context values name `NRFreqRelation=<cell>-<layer>`) |

Enactment report: `intentFulfilmentReport` with per-expectation and per-target results
(`FULFILLED`, or `NOT_FULFILLED` / `DEGRADED`; `targetAchievedValue` on success) and `additionalFulfilmentInfo` as
a JSON string `{"actions": [...]}` carrying the DME action ids and forwarded RAN NF OAM job ids. The intent is
`FULFILLED` only when every expectation applied with `COMPLETED` status and no target was unsupported.

### 2.5 Interactions

| Trigger | Outbound call | Failure behaviour |
|---|---|---|
| Remedial action | See the dispatch table | Non-2xx recorded as `ESCALATED`; a transport exception propagates as 500 |
| Registration | `DELETE` then `POST /intent-service/intent-handling-functions` with `notificationDestination = SA_SMOS_O1_CM_HANDLER_URL` | Non-201 → 422 |
| `enact_intent` | `GET /intent-service/intents/{id}`; `GET /intent-service/intent-handling-functions` (CM targets); `POST /dme/actions` per expectation with `{requestedBy, changes, actionId, sourceContext {intentId, expectationId, rmioId}}`; `POST /intent-service/intent-reports` | A DME status other than 200/202 becomes `HTTP_<code>` and degrades the report; if the report publish fails the enactment is still recorded with a null `intent_report_id` |

Inbound: Intent Service pushes one `POST /o1-cm-handler/intents` per new intent (best-effort on its side, no
retry). No background tasks.

### 2.6 Configuration

| Variable | Default | Use |
|---|---|---|
| `SA_SMOS_O1_CM_HANDLER_URL` | `http://sa-smos:8000/o1-cm-handler/intents` | The `notificationDestination` registered at Intent Service |
| `SMO_DATABASE_URL` | `postgresql+psycopg://smo:smo@postgres:5432/smo` | Database |
| `R1_GATEWAY_URL` | `http://r1-termination:8000` | Outbound R1 base URL |
| `SMO_INVOKER_ID`, `SMO_INVOKER_SECRET` | unset (self-onboards at SME) | Outbound OAuth2 identity |

### 2.7 Error codes

| Code | HTTP | When |
|---|---|---|
| `ASSURANCE_MONITOR_NOT_FOUND` | 404 | Unknown monitor on read or remedial action |
| `ROLLBACK_HISTORY_UNAVAILABLE` | 409 | `ROLLBACK` for a monitor that is not rApp-instance-scoped |
| `COORDINATION_GROUP_MISMATCH` | 422 | More than one target on a monitor; unknown `actionType` (the code is reused for both) |
| `INTENT_NOT_FOUND` | 404 | O1-CM push for an intent Intent Service does not know |
| `RMIH_CAPABILITY_MISMATCH` | 422 | O1-CM push for an intent addressed to another RMIH |
| `SCHEMA_VALIDATION_FAILED` | 422 | CM target name not `<IOC>.<attribute>`; Intent Service refused the registration |
| FastAPI validation (`HTTPValidationError`, 422) | 422 | Malformed query or body |

### 2.8 Limits and open items

- `SCALE` always escalates: NFO scaling is a Phase 1 stub.
- `CONFIG_CHANGE` posts a RAN NF OAM config job with an empty `changes` list; it demonstrates the dispatch, it does
  not change configuration. Real CM changes go through the O1-CM handler.
- `evaluate` on an unknown monitor and `escalate` on an unknown monitor are not handled (500, and an orphan row
  where the database does not enforce the FK, respectively).
- `analytics_subscription_id` is stored; nothing subscribes or evaluates automatically. Evaluation is
  caller-driven.
- `auto_execution_scope_config` is never read or written by a route.
- The O1-CM handler enacts only `IS_EQUAL_TO` targets on `RAN_SUBNETWORK` objects; other conditions are reported
  unsupported.
- No `../OPEN_ITEMS.md` entry is specific to SA SMOS.

## 3. Unit tests

### 3.1 Running them

```bash
cd smo/sa-smos && PYTHONPATH=.:../shared python -m pytest tests/ -q
```

### 3.2 What is covered

| File | Covers | Tests |
|---|---|---|
| `tests/test_main.py` | Monitor registration and the single-target rule, threshold evaluation, CONFIG_CHANGE, SCALE escalation, ROLLBACK (409 without an rApp target, dispatch, rApp Management refusal escalated), RECONNECT (order, rApp-scoped, escalations), coordination-group retrain and its escalation, unknown monitor 404, escalate, list/get, remedial-action filters, `/health` | 21 |
| `tests/test_o1cm.py` | Registration and declared CM targets, per-cell enactment and FULFILLED report, failed config job reported DEGRADED, unenactable targets reported not written, DEACTIVATED / foreign / unknown intents, replay with the same action id, CIO on each relation, tilt and power per cell | 11 |

### 3.3 What is not covered here

The real DME to RAN NF OAM to O1 adaptor chain, a real Intent Service round trip, and the real NFO / rApp
Management (all stubbed). `tests_integration/test_cross_service.py` runs the O1-CM handler end to end in the
in-process mesh, and the closed-loop rApp suites exercise it through autonomy dispatch.

## 4. References

- Call flows: [04 closed-loop assurance](../docs/call-flows/04-closed-loop-assurance.md), [09 Intent Service intent flow](../docs/call-flows/09-intent-service-intent-flow.md) (O1-CM handler block), [22](../docs/call-flows/22-energy-saving-closed-loop.md), [23](../docs/call-flows/23-mobility-optimization-closed-loop.md), [24](../docs/call-flows/24-coverage-optimization-closed-loop.md), [25](../docs/call-flows/25-traffic-steering-closed-loop.md)
- OpenAPI: [`../docs/openapi/sa-smos.json`](../docs/openapi/sa-smos.json)
- Specs: [`TS28312_IntentNrm.yaml`](../../specs/5G_APIs/TS28312_IntentNrm.yaml)
- Related: [`../intent-service/README.md`](../intent-service/README.md), [`../so-smos/README.md`](../so-smos/README.md), [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md), [`../OPEN_ITEMS.md`](../OPEN_ITEMS.md)
