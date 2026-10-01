# RAN NF OAM (`ran-nf-oam/`)

> The one platform service that speaks O1 to RAN functions: it keeps the O1 adaptor / managed-entity registry and the per-vendor capability registry, dispatches schema-checked CM writes as NETCONF `edit-config` or RFC 8040 RESTCONF requests (by the ME's provisioned protocol), and carries alarms, PM, FM and software-management jobs.

| | |
|---|---|
| Standards basis | O-RAN O1 + 3GPP MnS (TS 28.532/28.541 CM, FM, PM, SWM) + internal per-vendor capability registry |
| R1 route / port | `/ran-nf-oam` via R1 Termination (container :8000) |
| Depends on (over R1) | DME (`/dme/production-capabilities`, `/dme/dme-types`, `/dme/data-jobs`, `/dme/data-jobs/{id}/records`); southbound (not R1): each ME's O1 adaptor over HTTP |
| Called by | DME (`POST /config-jobs`, O1 action mediation), SO SMOS (`POST /config-jobs`), SA SMOS (`POST /config-jobs`), SDK `sdk.data` (`cell-guards`, `managed-entities`, `vendor-capabilities`, `capabilities`, `…/config`), reference rApps (`GET /alarms`, `POST /pm-reports`), GUI / GUI BFF |
| Database tables | `o1_adaptor_endpoint`, `managed_entity`, `alarm`, `cm_schema_cache`, `vendor_capability`, `write_config_job`, `write_config_sub_change`, `pm_subscription`, `fm_subscription`, `software_management_job` |
| Unit tests | 101 passed (`tests/`, SQLite, standalone) |
| Status | Done for NETCONF-shaped and RESTCONF O1 CM dispatch. Open: alarm-storm correlation (`OI-1-alarm-storm`), alarm cell reference (`W10-alarm-cellref`), TS 28.319 MSAC (`SA-RANOAM-1`), TS 28.532 file/streaming reporting (`SA-RANOAM-8`); see [section 2.8](#28-limits-and-open-items) |

## 1. High-level design (HLD)

### 1.1 Purpose and scope

RAN NF OAM is the O1 termination of the SMO. Everything the platform needs to read from or write to a RAN function over O1 goes through it, so that no other module knows a wire protocol, a vendor data model or an adaptor address.

It provides:

- an **endpoint registry**: O1 adaptors self-register per managed element (ME); each endpoint has a heartbeat-driven health state;
- a **per-vendor capability registry** (the multi-vendor framework): which MnS services a vendor implements, whose data model its CM follows, which O1 transports it speaks; plus CM schema descriptors and per-cell guard attributes (see [O1 vendor onboarding](#o1-vendor-onboarding));
- **CM writes**: `WriteConfigJob` with per-attribute sub-changes, dispatched as RFC 6241 `edit-config`, retried, aggregated, and read back with `get-config`;
- **FM**: fleet-unique alarm records with the TS 28.532/28.111 fault fields, ack and clear;
- **PM**: `SubscribePM` (a DME producer registration) and the PM data path O1 PM -> RAN NF OAM -> DME;
- **SWM**: a download / install / activate job lifecycle;
- **FM subscriptions**: a DME producer registration for `RAN.FaultRecords`.

It does not decide anything: what to change is decided by rApps (via DME action mediation) and what is allowed is decided by the registry data.

### 1.2 Standards basis

| Spec | What is realised | What is deliberately not |
|---|---|---|
| O-RAN O1 / 3GPP TS 28.532 ProvMnS ([`TS28532_ProvMnS.yaml`](../../specs/5G_APIs/TS28532_ProvMnS.yaml)) | Write path as RFC 6241 `<edit-config>` with a per-`<managed-object>` `operation` (`merge` / `replace` / `create` / `delete` / `remove`); `<get-config>` read-back. For an ME provisioned `RESTCONF`, the same operations as RFC 8040 requests on the `managed-element={ref}[/managed-function={fref}]` data resource (`merge` PATCH, `replace` PUT, `create` POST on the parent, `delete` / `remove` DELETE; `application/yang-data+json`, RFC 7951); read-back is a GET | No SSH/NETCONF session (XML over plain HTTP to the adaptor); no TLS or auth on RESTCONF; no HTTP-verb ProvMnS; no RESTCONF notifications, YANG-patch or query parameters |
| TS 28.541 NR NRM ([`TS28541_NrNrm.yaml`](../../specs/5G_APIs/TS28541_NrNrm.yaml)) | Bundled CM descriptor `3gpp-ts28541-nrnrm@19.6.0` (54 IOC classes) as the default spec data model | WG10 O1NRM / WG5 IOCs are not bundled (`SA-O1-4`) |
| TS 28.532 FaultMnS / TS 28.111 ([`TS28111_FaultNrm.yaml`](../../specs/5G_APIs/TS28111_FaultNrm.yaml)) | `AlarmRecord` fields: `alarmType`, `probableCause`, `specificProblem`, `rootCauseIndicator`, `correlatedNotifications`, `proposedRepairActions`, `ackUserId`, `alarmChangedTime`; clear = `severity` `cleared` (as NotifyClearedAlarm reuses `perceivedSeverity`) | `severity` is this build's lowercase vocabulary, not the six TS 28.111 values (`SA-RANOAM-6-severity`); flat ME/MF reference strings, not DNs (`SA-RANOAM-4`) |
| TS 28.550 PerfMeasJobCtrlMnS ([`TS28550_PerfMeasJobCtrlMnS.yaml`](../../specs/5G_APIs/TS28550_PerfMeasJobCtrlMnS.yaml)) | `granularityPeriod` on a PM subscription | The clause-8 job-control surface (schedule, priority, reportingPeriod) is out; SubscribePM is a DME-producer registration, not a clause-8 call |
| TS 28.532 file / streaming / heartbeat ([`FileDataReporting`](../../specs/5G_APIs/TS28532_FileDataReportingMnS.yaml), [`StreamingData`](../../specs/5G_APIs/TS28532_StreamingDataMnS.yaml), [`HeartbeatNtf`](../../specs/5G_APIs/TS28532_HeartbeatNtf.yaml)) | The service names exist in the registry's `MnsService` vocabulary (`FILE`, `STREAM`, `HEARTBEAT`) and can be declared | No file or streaming reporting is implemented (`SA-RANOAM-8`); the heartbeat is the adaptor's own `POST /o1-adaptor-endpoints/{id}/heartbeat` |
| O1 adaptor MnS hierarchy mapping ([`O1_Adaptor_MnS_Hierarchy_Mapping_v4.xlsx`](../../specs/O1_Adaptor/O1_Adaptor_MnS_Hierarchy_Mapping_v4.xlsx)) | Basis of the eight MnS service categories in the capability registry | MnS Registry NRM polling does not exist; adaptors self-register |
| TS 28.319 MSAC | A presence check of `msacRole` for `scope == "entire-RAN"` | No Identity / Role / AccessRule evaluation (`SA-RANOAM-1`) |
| O-RAN WG4 O-RU M-plane | The Software Management RPC lifecycle (download / install / activate) as a job state machine | No M-plane YANG; `ru_instance_id` is stored, not used |

### 1.3 Position in the platform

```
 rApp -> DME  POST /dme/actions ----R1----> RAN NF OAM  POST /config-jobs
 SO SMOS / SA SMOS -----------------R1----> RAN NF OAM  POST /config-jobs
 rApps / SDK  GET /alarms, /cell-guards, /managed-entities/{me}/config ... R1 ...> RAN NF OAM
 NF PM report -> POST /pm-reports ----------> RAN NF OAM --R1--> DME records (RAN.PMCounters.<counter>)
                                                  |
                                   edit-config / get-config (XML over HTTP, not R1)
                                                  v
                                       per-ME O1 adaptor (adaptor_uri)
```

- It calls DME over R1 only to register itself as a producer (`RAN.PMCounters.<counter>`, `RAN.FaultRecords`) and to fan PM measurements out as DME records. Consumers never read RAN NF OAM for PM; they read DME.
- It talks to adaptors directly (southbound, outside R1) using `netconf_client.py` or `restconf_client.py`. The adaptor address is `adaptor_uri` from its own registry, never a URL taken from a request body, except where noted in [onboarding discovery](#operator-steps).
- It never calls AIMgF, MLMR, MLLF, MDAF, Intent Service, NFO or FOCOM. MDAF is never on the action path.
- Test vendor: [`mock-o1-adaptor`](../mock-o1-adaptor/README.md).
- DME / RAN NF OAM boundary: [DME](../dme/README.md) owns the type / producer registry, data jobs, `DataRecord` and `DmeActionRecord` + `POST /actions` (O1 action mediation); RAN NF OAM owns the O1 protocol dispatch, endpoint registry, ME/MF addressing, alarms and PM/CM/SWM jobs. DME's record is the audit of what the AI/ML decision asked for; RAN NF OAM's `WriteConfigJob` is the record of what NETCONF or RESTCONF did. A 4xx from RAN NF OAM (capability or schema refusal) is passed back by DME unchanged and DME records the action `REJECTED`.

### 1.4 Ownership

| Owns | Does not own → owner |
|---|---|
| O1 adaptor endpoint registry and health (`O1AdaptorEndpoint`) | The decision to change a cell → rApp / Intent Service / SA SMOS |
| `ManagedEntity` (ME / MF addressing, vendor, protocol, cell guards) | Action audit and idempotency (`DmeActionRecord`) → DME |
| Vendor capability registry, CM schema descriptors | Data records, data jobs, type registry → DME |
| CM write jobs and sub-changes, NETCONF / RESTCONF dispatch, retry, read-after-write | Analytics on PM / alarms → MDAF |
| Alarms (ingest, ack, clear) and FM / PM subscriptions | Infrastructure (O-Cloud) alarms → FOCOM (`OCloudAlarm`) |
| SWM job lifecycle | RAN-function placement and runtimes → NFO |
| PM data path into DME | A1 policy → A1 Related |

### 1.5 Design decisions

- **Onboarding is data, not code.** A vendor is a `VendorCapability` row plus optional CM schema descriptors. Two generic checks read it on every O1 operation; there is no per-vendor branching in the code ([O1 vendor onboarding](#o1-vendor-onboarding)).
- **Permissive default.** An ME whose vendor has no registered capability skips both checks, so single-vendor deployments work with no onboarding.
- **Reject before dispatch.** Service-presence and schema checks run before a `WriteConfigJob` exists. A refused write creates nothing and never reaches the adaptor.
- **Per-attribute atomicity, framework aggregation.** Each sub-change is one atomic `edit-config`. `PARTIAL_SUCCESS` is an aggregation over several atomic calls, computed in `aggregate_event`.
- **Retry only transient failures.** Timeouts and unreachable adaptors are retried (immediately, then +5 s, +10 s, +20 s); an `<rpc-error>` or an unusable reply is a definite answer and is never retried. Exhausting retries raises an alarm on the ME.
- **Read-after-write.** `GET /managed-entities/{me}/config` reads the live running config via `get-config`, so a caller can verify a write took effect (a lying agent is detectable).
- **Live health aging.** No scheduler exists: an `ACTIVE` endpoint that missed its heartbeat window (90 s) is aged to `DEGRADED` at the moment it is consulted (config dispatch) or by the bulk `POST /o1-adaptor-endpoints/discover`.
- **Alarm ids are always minted here** (fresh UUID), never the raising ME's native id, so ids cannot collide across a fleet.
- **Safe parsing.** Adaptor replies are parsed with `defusedxml`; entity-expansion or external-entity XML is treated like an unparseable reply.
- **Failure behaviour toward callers.** Per-change failures (unreachable endpoint, protocol not supported, RPC failure) are recorded as sub-change `REJECTED` with a `rejectionReason`, and the job ends `FAILED` or `PARTIAL_SUCCESS`; the HTTP response is still `202`. Registry or pre-check refusals are 4xx `ProblemDetails`.
- **Security / RBAC.** No in-module authorization; R1 Termination introspects every token. The GUI BFF restricts `vendor-onboarding`, `cm-schemas`, `vendor-capabilities` writes, cell-guard writes, `alarms/ingest` and endpoint `heartbeat` to admin, and `config-jobs`, subscriptions, SWM and endpoint registration to operator. `scope == "entire-RAN"` additionally needs a non-empty `msacRole`.

## 2. Low-level design (LLD)

### 2.1 Code map

| File | Responsibility |
|---|---|
| `app/main.py` | App, endpoint registry, `POST /config-jobs` (pre-check, dispatch loop, aggregation; `_o1_client` picks the NETCONF or RESTCONF client by `o1_protocol`), alarms, PM / FM subscriptions and PM report fan-out, SWM jobs, health aging, list reads, DME callback stubs (`/health`, `/dme-jobs`) |
| `app/vendors.py` | Capability registry, CM schemas, onboarding flow, managed entities, cell guards, and the two request-time checks (`require_service`, `schema_problems`); mounted as a router |
| `app/netconf_client.py` | RFC 6241 `edit-config` / `get-config` RPC builders, HTTP transport, `EditResult` (reason, retryable) |
| `app/restconf_client.py` | RFC 8040 client: data-resource URL (percent-encoded keys), `yang-data+json` body, edit `operation` -> PATCH / PUT / POST / DELETE, GET read-back, `RestconfResult` (reason, retryable, `error_tag`); reuses `EditResult` and the 30 s timeout |
| `app/statemachine.py` | Three FSMs: `WriteConfigJob`, `SoftwareManagementJob`, endpoint health; `aggregate_event` |
| `app/models.py` | SQLAlchemy models |
| `app/cm_schemas/3gpp-ts28541-nrnrm.json` | Bundled TS 28.541 NR NRM descriptor (default `specSchemaRef`) |
| `../scripts/ingest_cm_schema.py` | Offline generator of descriptors from NRM OpenAPI |

### 2.2 Data model

Cross-module references are bare strings or UUIDs; none exist here.

**`o1_adaptor_endpoint`**

| Column | Notes |
|---|---|
| `endpoint_id` | PK, UUID |
| `managed_element_ref` | unique, not null |
| `adaptor_uri` | where `edit-config` / `get-config` are POSTed |
| `protocol_support` | list of strings |
| `registered_via` | default `MNS_REGISTRY_NRM` |
| `health_status` | `DISCOVERED` / `ACTIVE` / `DEGRADED` / `UNREACHABLE`; registration sets `DISCOVERED` (the column default `ACTIVE` is only used by direct inserts) |
| `last_heartbeat_at` | set by heartbeat |
| `supported_services` | nullable; `NULL` = "whatever the vendor declares" |

**`managed_entity`**

| Column | Notes |
|---|---|
| `managed_element_ref` | PK |
| `managed_function_ref` | nullable (e.g. `NRCellDU=1`) |
| `entity_type`, `vendor_name` | `vendor_name` nullable; no vendor = unchecked |
| `o1_protocol` | `NETCONF` / `RESTCONF` |
| `o1_adaptor_endpoint_id` | FK to `o1_adaptor_endpoint` |
| `cell_guards` | JSON `{cellId: {cellClass, sectorGroup, incidentZone, neighbourRefs}}` |

**`vendor_capability`** (PK `vendor_name`): `supported_services` (list), `conformance_mode` (`OWN` / `SPEC` / `COMBINED`, default `SPEC`), `supported_vendor_modes` (list), `schema_name` / `schema_revision` (vendor descriptor), `spec_schema_name` / `spec_schema_revision` (spec descriptor), `discovery_uri`, `updated_at`.

**`cm_schema_cache`** (PK `schema_name` + `revision`): `location`, `type` (`YANG` / `OPENAPI_NRM` / `DESCRIPTOR`), `descriptor` JSON, `cached_at`. Bundled descriptors live in files, not in this table.

**`write_config_job`** (PK `job_id`): `requested_by`, `scope`, `schema_validated_at`, `status`, `conflict_resolution` (unused), `msac_role`.

**`write_config_sub_change`** (PK `id`, FK `job_id`): `managed_element_ref`, `managed_function_ref`, `attribute_changes` JSON, `operation` (default `merge`), `status` (`PENDING` / `APPLIED` / `REJECTED`), `rejection_reason`, `attempts`.

**`alarm`** (PK `alarm_id`, always a fresh UUID; FK `managed_element_ref` to `managed_entity`): `source_alarm_id`, `managed_function_ref`, `severity`, `ack_state` (`UNACKNOWLEDGED` / `ACKNOWLEDGED`), `correlation_group`, `raised_at`, `probable_cause`, `specific_problem`, `root_cause_indicator`, `correlated_notifications` (UUID list), `proposed_repair_actions`, `alarm_type`, `cleared_at`, `clear_user_id`, `ack_user_id`, `changed_at`. There is no cell reference column (`W10-alarm-cellref`).

**`pm_subscription`** (PK `subscription_id`, FK ME): `counter_type`, `delivery_method`, `southbound_engine`, `granularity_period`.

**`fm_subscription`** (PK `subscription_id`, FK ME): `delivery_method`, `southbound_engine`.

**`software_management_job`** (PK `job_id`, FK ME): `ru_instance_id` (reserved), `phase` (`DOWNLOAD` / `INSTALL` / `ACTIVATE`), `status`.

The Postgres schema (`migrations/001_init.sql`) adds CHECK constraints that the code does not pre-validate: `alarm.severity` in {`critical`, `major`, `minor`, `warning`, `cleared`}, `alarm.ack_state`, `alarm.alarm_type` (the 11 TS 28.111 values), PM / FM `delivery_method` in {`pull`, `push`, `stream`}. SQLite unit tests do not enforce them.

### 2.3 State machines

**`WriteConfigJob`** (`statemachine.py`)

| From | Event | To |
|---|---|---|
| `PENDING` | `PRECHECK_PASS` | `PROCESSING` |
| `PENDING` | `PRECHECK_FAIL` | `FAILED` |
| `PROCESSING` | `AGGREGATE_ALL_APPLIED` | `COMPLETED` |
| `PROCESSING` | `AGGREGATE_ALL_REJECTED` | `FAILED` |
| `PROCESSING` | `AGGREGATE_MIXED` | `PARTIAL_SUCCESS` |

`aggregate_event` maps sub-change statuses to the aggregate event: some applied and some rejected = mixed; all applied = all-applied; otherwise (including no sub-changes) = all-rejected. In the HTTP flow the pre-check runs before the job exists, so `PRECHECK_FAIL` is only exercised in unit tests. `COMPLETED`, `FAILED` and `PARTIAL_SUCCESS` are terminal. Any other transition raises `IllegalTransition`.

**`SoftwareManagementJob`** (status only; phase is separate data advanced with the events)

| From | Event | To | Phase effect |
|---|---|---|---|
| `PENDING` | `START` | `IN_PROGRESS` | `DOWNLOAD` |
| `IN_PROGRESS` | `DOWNLOAD_OK` | `IN_PROGRESS` | -> `INSTALL` |
| `IN_PROGRESS` | `INSTALL_OK` | `IN_PROGRESS` | -> `ACTIVATE` |
| `IN_PROGRESS` | `ACTIVATE_OK` | `COMPLETED` | stays `ACTIVATE` |
| `IN_PROGRESS` | `PHASE_FAILED` | `FAILED` | unchanged |

`COMPLETED` and `FAILED` are terminal; advancing a terminal job raises `IllegalTransition` (unhandled, so HTTP 500 today).

**Endpoint health**

| From | Event | To | Trigger |
|---|---|---|---|
| `DISCOVERED` | `HEARTBEAT` | `ACTIVE` | `POST /o1-adaptor-endpoints/{id}/heartbeat` |
| `ACTIVE` | `MISSED_HEARTBEATS` | `DEGRADED` | live aging: last heartbeat older than 90 s, at config dispatch or `POST /o1-adaptor-endpoints/discover` |
| `DEGRADED` | `HEARTBEAT` | `ACTIVE` | heartbeat |
| `DEGRADED` | `DEREGISTERED` | `UNREACHABLE` | defined, no route fires it |
| `UNREACHABLE` | `RE_REGISTERED` | `DISCOVERED` | defined, no route fires it |

`ACTIVE` cannot go straight to `UNREACHABLE`. An `ACTIVE` endpoint that has never heartbeated is not aged. A heartbeat on an `ACTIVE` or `UNREACHABLE` endpoint only refreshes `last_heartbeat_at`. A config change to a `DEGRADED` or `UNREACHABLE` endpoint is rejected with `ENDPOINT_UNREACHABLE` without dispatch.

### 2.4 API

All routes are under `/ran-nf-oam` through R1. Lists return `{items, total, limit, offset}`.

**O1 adaptor endpoints and managed entities**

| Method | Path | Purpose / notable errors |
|---|---|---|
| POST | `/o1-adaptor-endpoints` | Register an adaptor and its ME (201, `healthStatus` `DISCOVERED`). Body: `managedElementRef`, `adaptorUri`, `protocolSupport`, `o1Protocol`, `entityType`, `managedFunctionRef?`, `vendorName?`, `supportedServices?`. 409 `PROTOCOL_NOT_SUPPORTED`, 422 `SCHEMA_VALIDATION_FAILED` |
| GET | `/o1-adaptor-endpoints` | List; filter `health_status` |
| POST | `/o1-adaptor-endpoints/discover` | Bulk heartbeat-aging sweep; returns `{checked}` |
| POST | `/o1-adaptor-endpoints/{endpoint_id}/heartbeat` | Heartbeat; `DISCOVERED` / `DEGRADED` -> `ACTIVE` |
| GET | `/managed-entities` | List; filter `vendor_name`. Items carry effective `supportedServices`, `conformanceMode`, `cellGuards` |
| GET | `/managed-entities/{me}` | One ME; 404 `MANAGED_ENTITY_NOT_FOUND` |
| GET | `/managed-entities/{me}/config` | Read-after-write via `get-config`; query `managed_function_ref`. 409 `O1_SERVICE_NOT_SUPPORTED` (PROV), 503 `ENDPOINT_UNREACHABLE` |
| PUT / DELETE | `/managed-entities/{me}/cells/{cell}/guards` | Set / remove a cell guard (`cellClass` `EMERGENCY` / `COVERAGE_CRITICAL` / `NORMAL`, `sectorGroup`, `incidentZone`, `neighbourRefs`); DELETE is idempotent |
| GET | `/cell-guards` | Guard query across MEs; filters `managed_element_ref`, `cell_id`, `cell_class`, `sector_group`, `incident_zone` |

**Vendor registry, schemas, onboarding** (full semantics in [O1 vendor onboarding](#o1-vendor-onboarding))

| Method | Path | Purpose |
|---|---|---|
| PUT | `/vendor-capabilities/{vendor}` | Declare / replace a vendor capability |
| GET | `/vendor-capabilities`, `/vendor-capabilities/{vendor}` | List / read; 404 `VENDOR_CAPABILITY_NOT_FOUND` |
| DELETE | `/vendor-capabilities/{vendor}` | Remove (idempotent) |
| GET | `/capabilities` | Aggregate: union of vendor modes, `mnsServices`, per-vendor summary |
| POST | `/cm-schemas` | Load a descriptor (201); 409 `CM_SCHEMA_CONFLICT` |
| GET | `/cm-schemas` | Bundled then loaded descriptors, with `classCount`, `builtin` |
| GET | `/cm-schemas/{name}?revision=` | One descriptor in full; 404 `CM_SCHEMA_NOT_FOUND` |
| POST | `/vendor-onboarding` | Discover, load schemas, declare capability in one call (201) |

**CM write jobs**

| Method | Path | Purpose / notable errors |
|---|---|---|
| POST | `/config-jobs` | `WriteConfigurationChanges` (202 `{jobId, status}`). Body: `requestedBy`, `scope`, `changes[]` (`managedElementRef`, `managedFunctionRef?`, `className?`, `attributeChanges?`, `operation?`), `msacRole?`. 403 `MSAC_ACCESS_DENIED`, 409 `O1_SERVICE_NOT_SUPPORTED`, 422 `SCHEMA_VALIDATION_FAILED` |
| GET | `/config-jobs/{job_id}` | Job with `subChanges` (`operation`, `status`, `rejectionReason`, `attempts`) |
| GET | `/config-jobs` | List; filter `status` |

**Alarms and subscriptions**

| Method | Path | Purpose / notable errors |
|---|---|---|
| POST | `/alarms/ingest` | Query parameters: `source_alarm_id`, `managed_element_ref`, `severity`, optional fault fields. Returns `{alarmId}`. 409 `O1_SERVICE_NOT_SUPPORTED` (FM) |
| GET | `/alarms` | List; filters `managed_element_ref`, `severity` (`cleared` isolates history) |
| PATCH | `/alarms/{id}/ack` | `new_state`, `ack_user_id?` |
| PATCH | `/alarms/{id}/clear` | Sets `severity=cleared`, `cleared_at`, `clear_user_id?`; alarm stays listed |
| POST | `/pm-subscriptions` | Query: `managed_element_ref`, `counter_type`, `delivery_method`, `granularity_period?`. Registers a DME producer. 409 `O1_SERVICE_NOT_SUPPORTED` (PM) |
| GET / DELETE | `/pm-subscriptions`, `/pm-subscriptions/{id}` | List (filter ME) / delete (idempotent) |
| POST | `/pm-reports` | NF PM report -> DME records (201). 409 (PM), 422 `SCHEMA_VALIDATION_FAILED` when no PM subscription exists for ME + counter |
| POST | `/fm-subscriptions` | Registers the `RAN.FaultRecords` DME producer. 409 `O1_SERVICE_NOT_SUPPORTED` (FM) |
| GET / DELETE | `/fm-subscriptions`, `/fm-subscriptions/{id}` | List (filter ME) / delete (idempotent) |

**Software management**

| Method | Path | Purpose |
|---|---|---|
| POST | `/software-management-jobs` | Start (202, `PENDING` -> `IN_PROGRESS`, phase `DOWNLOAD`); query `managed_element_ref`, `ru_instance_id?`. 409 `O1_SERVICE_NOT_SUPPORTED` (SWM) |
| POST | `/software-management-jobs/{job_id}/advance` | `succeeded` true advances the phase, false fails the job |
| GET | `/software-management-jobs` | List; filter ME |

**Liveness and DME callbacks**

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness; also the `producerHealthCallbackUrl` registered with DME |
| POST | `/dme-jobs` | `jobCallbackUrl` registered with DME; acks only (`{"status": "accepted"}`), no per-job state |
| DELETE | `/dme-jobs/{data_job_id}` | 204, no-op |

PM report body: `managedElementRef`, `counterType`, `measurements[]` each with `cellId`, `timestamp`, and `value` and/or `values` (a dict of several counters of one family, e.g. handover counters), plus optional `relation` (neighbour relation such as `201-202`). A measurement with neither is rejected.

### 2.5 Interactions

**CM write dispatch (`POST /config-jobs`)**

1. `scope == "entire-RAN"` without `msacRole` -> 403 `MSAC_ACCESS_DENIED`.
2. For every change: `require_service(PROV)`, then `schema_problems`. Any problem -> 422, nothing is created.
3. A `WriteConfigJob` is created; `schema_validated_at` set; `PENDING` -> `PROCESSING`.
4. Per change, in order: ME missing or without endpoint -> sub-change `REJECTED` `ENDPOINT_UNREACHABLE`; endpoint aged, then `DEGRADED` / `UNREACHABLE` -> `REJECTED` `ENDPOINT_UNREACHABLE`; `o1_protocol` neither `NETCONF` nor `RESTCONF` (`_o1_client` finds no client) -> `REJECTED` `PROTOCOL_NOT_SUPPORTED`; otherwise dispatch.
5. Dispatch follows the ME's `o1_protocol` (timeout 30 s per exchange). NETCONF: POSTs an `edit-config` XML RPC to `adaptor_uri`; reasons `NETCONF_TIMEOUT` (client timeout, 408, 504), `NETCONF_UNREACHABLE` (connection error, 5xx), `NETCONF_RPC_FAILED` (other 3xx/4xx, unparseable reply, or no `<ok/>`). RESTCONF: `adaptor_uri` is the RESTCONF root; the `operation` maps to `merge` PATCH, `replace` PUT, `create` POST on the parent, `delete` / `remove` DELETE (`remove` tolerates `data-missing`) on `{root}/data/managed-element={ref}[/managed-function={fref}]`; reasons `RESTCONF_TIMEOUT` (client timeout, 408, 504), `RESTCONF_UNREACHABLE` (connection error, 502, 503, or a 5xx without an `ietf-restconf:errors` body), `RESTCONF_REQUEST_FAILED` (any other non-2xx, including any `ietf-restconf:errors` reply, or an unknown operation). Only the timeout and unreachable reasons are retried; an error reply is never retried.
6. Attempts are made after delays `0, 5, 10, 20` s (`RAN_NF_OAM_NETCONF_RETRY_DELAYS`); `attempts` is stored per sub-change. A change that failed after more than one attempt raises an alarm on the ME (`severity=major`, `alarm_type=COMMUNICATIONS_ALARM`, `probable_cause` = the last reason, `source_alarm_id=o1-config:<jobId>:<target>`).
7. Aggregate: all applied -> `COMPLETED`; all rejected -> `FAILED`; mixed -> `PARTIAL_SUCCESS`.

RPC shape: an `<rpc>` whose `message-id` is the job id, containing `<edit-config><target><running/></target><config>` and one `<managed-object ref="ME" function-ref="MF" operation="op">` holding one child element per attribute. `get-config` uses `<source><running/></source>` and a `<filter>` with the same managed-object node. Success is an `<rpc-reply>` containing `<ok/>`.

**PM path.** `POST /pm-subscriptions` stores the subscription, then `POST /dme/production-capabilities` (`namespace RAN`, `name PMCounters.<counter>`, `producerId ran-nf-oam`, health and job callbacks on `http://ran-nf-oam:8000`). The southbound engine is `ProvMnS` (pull), `PMJobControl` (push), `StreamingDataReporting` (stream), else `FileDataReporting`. `POST /pm-reports` then looks up the DME type `RAN.PMCounters.<counter>`, lists its data jobs (up to 500) and posts one record per measurement and job (`managedElementRef`, `cellId`, `counter`, `value`, `timestamp`, optional `values`, `relation`). With no matching DME type or no jobs, the report is accepted with `recordsDelivered: 0`. A DME failure is not caught here.

**FM path.** `POST /fm-subscriptions` registers one shared `RAN.FaultRecords` DME type, joined across every subscribing ME. It gives consumers DME-mediated visibility of alarms; it gives DME and rApps no way to clear an alarm: clearing stays `PATCH /alarms/{id}/clear`.

**Inbound from DME.** `/health` and `/dme-jobs` answer the callbacks registered above.

### 2.6 Configuration

| Variable | Default | Meaning |
|---|---|---|
| `RAN_NF_OAM_NETCONF_RETRY_DELAYS` | `0,5,10,20` | Seconds before each dispatch attempt (4 attempts); applies to NETCONF and RESTCONF alike |
| `SMO_DATABASE_URL` | `postgresql+psycopg://smo:smo@postgres:5432/smo` | Database (shared lib) |
| `R1_GATEWAY_URL` | `http://r1-termination:8000` | R1 gateway for DME calls (shared lib) |
| `SMO_INVOKER_ID`, `SMO_INVOKER_SECRET` | unset | R1Client credentials (shared lib) |

Constants in code: `MISSED_HEARTBEAT_THRESHOLD` = 90 s; `NETCONF_TIMEOUT_SECONDS` = `RESTCONF_TIMEOUT_SECONDS` = 30; default spec schema `3gpp-ts28541-nrnrm@19.6.0`; producer callback host `http://ran-nf-oam:8000`.

### 2.7 Error codes

ProblemDetails are returned as `{"detail": {"type": "about:blank", "title": <code>, "status", "detail"}}`; the code is the `title`.

| Code | HTTP | When |
|---|---|---|
| `MSAC_ACCESS_DENIED` | 403 | `scope` `entire-RAN` without `msacRole` |
| `O1_SERVICE_NOT_SUPPORTED` | 409 | The ME's effective services lack the required MnS service (see [checks](#checks-at-request-time)) |
| `PROTOCOL_NOT_SUPPORTED` | 409 | Endpoint registration or capability declaration with an `o1Protocol` the vendor has not declared; also the sub-change `rejectionReason` for an ME whose provisioned protocol is neither NETCONF nor RESTCONF at dispatch, and the error of `GET .../config` for such an ME |
| `CM_SCHEMA_CONFLICT` | 409 | A different descriptor at an existing `schemaName` + `revision`, or `POST /cm-schemas` of one already loaded |
| `SCHEMA_VALIDATION_FAILED` | 422 | CM write refused by the schema check; bad descriptor shape; `OWN` / `COMBINED` without `schemaRef`; endpoint `supportedServices` wider than the vendor's; onboarding with no discoverable services or a vendor mismatch; PM report without a subscription |
| `CM_SCHEMA_NOT_FOUND` | 404 | Unknown `schemaRef` / `specSchemaRef`, or `GET /cm-schemas/{name}` |
| `VENDOR_CAPABILITY_NOT_FOUND` | 404 | `GET /vendor-capabilities/{vendor}` |
| `MANAGED_ENTITY_NOT_FOUND` | 404 | ME lookups (`GET /managed-entities/{me}`, cell guards, onboarding `discoverFrom`) |
| `ENDPOINT_UNREACHABLE` | 503 | the configuration read (`get-config` or RESTCONF GET) failed or ME has no adaptor; onboarding discovery failed or ME has no adaptor. As a sub-change `rejectionReason` it also means the endpoint was missing / `DEGRADED` / `UNREACHABLE` |
| `NETCONF_TIMEOUT`, `NETCONF_UNREACHABLE`, `NETCONF_RPC_FAILED`, `RESTCONF_TIMEOUT`, `RESTCONF_UNREACHABLE`, `RESTCONF_REQUEST_FAILED` | n/a | Sub-change `rejectionReason` values only |

### 2.8 Limits and open items

- **Transport.** RFC 6241-shaped `edit-config` and RFC 8040 RESTCONF requests, both over plain HTTP (no TLS, auth, notifications or YANG-patch), are dispatched; an ME provisioned for any other protocol is rejected at dispatch with `PROTOCOL_NOT_SUPPORTED`. A new transport needs one client module per transport family, selected by `ManagedEntity.o1_protocol`.
- **YANG.** The ingestion script reads NRM OpenAPI only; a YANG bundle needs a YANG front end (`pyang`) emitting the same descriptor shape. Only the TS 28.541 descriptor ships (`SA-O1-4`).
- **Semantics.** A descriptor documents shape, not runtime behaviour; a vendor that silently ignores an accepted attribute is found only by integration testing against that vendor (`GET .../config` read-back exists for this).
- **Alarms.** `correlation_group` is a coarse string; no storm correlation (`OI-1-alarm-storm`). Alarms carry no cell reference (`W10-alarm-cellref`). `severity` / `alarm_type` / `ack_state` are not validated in code; out-of-vocabulary values fail the Postgres CHECK as a 500 (`SA-RANOAM-6-severity`).
- **Access control.** MSAC is a presence check (`SA-RANOAM-1`); `scope` collides with ProvMnS `ScopeType` (`SA-RANOAM-2`).
- **Addressing.** Flat reference strings, not DNs (`SA-RANOAM-4`, `SA-O1-1`).
- **File / streaming reporting** is absent (`SA-RANOAM-8`).
- **No scheduler.** Endpoint health is aged on use; there is no registry polling and no periodic discovery. `DEREGISTERED` and `RE_REGISTERED` are not fired by any route.
- **Unguarded reads.** `GET /config-jobs/{id}`, alarm ack / clear and SWM advance on an unknown id fail with an unhandled 500, not a 404. `POST /software-management-jobs/{id}/advance` on a terminal job is also a 500.
- **Phase 1 stubs.** `/dme-jobs` acks only; `ru_instance_id` and `conflict_resolution` are stored/unused; `PM`/`FM` `delivery_method` outside `pull`/`push`/`stream` is accepted by the code but rejected by the Postgres CHECK.

## O1 vendor onboarding

Onboarding a RAN vendor or a Digital Twin is data fed to RAN NF OAM, not new code (`ran-nf-oam/app/vendors.py`, [call flow 21](../docs/call-flows/21-o1-vendor-onboarding.md)). The registry is per vendor; two generic checks read it on every O1 operation.

### The three axes

A vendor's O1 termination differs on three independent axes:

| Axis | Question | Realized by |
|---|---|---|
| 1. MnS transport | Which wire protocol? | `ManagedEntity.o1_protocol`; vendor modes `O1_NETCONF` / `O1_RESTCONF`. RFC 6241-shaped `edit-config` and RFC 8040 RESTCONF, both over HTTP, are dispatched. |
| 2. MnS services | Does the vendor implement this operation category at all? (presence) | `supportedServices` ⊆ `PROV`, `FM`, `PM`, `FILE`, `STREAM`, `SWM`, `SUBSCRIPTION`, `HEARTBEAT` |
| 3. IOC data model | Whose class / attribute names and value ranges? (shape) | `conformanceMode` `SPEC` / `OWN` / `COMBINED` + CM schema descriptors |

### Registry resources

All routes are under `/ran-nf-oam` through R1.

| Resource | Routes |
|---|---|
| Vendor capability | `PUT /vendor-capabilities/{vendor}` (body: `supportedServices` ≥ 1, `conformanceMode` default `SPEC`, `supportedVendorModes` default `["O1_NETCONF"]`, `schemaRef`, `specSchemaRef`), `GET /vendor-capabilities`, `GET`/`DELETE /vendor-capabilities/{vendor}` |
| CM schema descriptors | `POST /cm-schemas` (`schemaName`, `revision`, `type` `YANG`/`OPENAPI_NRM`/`DESCRIPTOR`, `location`, `descriptor`), `GET /cm-schemas`, `GET /cm-schemas/{name}?revision=` |
| Onboarding flow | `POST /vendor-onboarding` |
| Aggregate capabilities | `GET /capabilities` (union of vendor modes, the MnS service list, per-vendor summary) |
| Managed entities | `GET /managed-entities?vendor_name=`, `GET /managed-entities/{me}` (effective services, conformance mode, cell guards) |
| Cell guards | `PUT`/`DELETE /managed-entities/{me}/cells/{cell}/guards` (`cellClass` `EMERGENCY` / `COVERAGE_CRITICAL` / `NORMAL`, `sectorGroup`, `incidentZone`, `neighbourRefs`), `GET /cell-guards?cell_class=&sector_group=&incident_zone=&managed_element_ref=&cell_id=` |

A descriptor has the shape `{"classes": {"<IOC>": {"<attribute>": {"type": ..., "enum"?: [...]}}}}`. The 3GPP TS 28.541 NR NRM descriptor `3gpp-ts28541-nrnrm@19.6.0` (54 IOC classes, generated from `specs/5G_APIs/TS28541_NrNrm.yaml`) is bundled in `ran-nf-oam/app/cm_schemas/` and is the default `specSchemaRef`.

### Operator steps

1. **Generate the data-model descriptor (offline, once per data model).** Descriptors are derived mechanically from NRM OpenAPI definitions, never hand-transcribed:

   ```
   cd smo && python scripts/ingest_cm_schema.py <NRM OpenAPI file>... \
       --name <schemaName> --revision <revision> --out <descriptor>.json
   ```

   Every `<IOC>-Single` schema becomes a class; its `attributes` (following `allOf` and `$ref` across sibling files) become the attributes a CM write may set. A `SPEC`-only vendor needs no descriptor of its own.

2. **The vendor's O1 adaptor registers its first managed element.**

   ```
   POST /ran-nf-oam/o1-adaptor-endpoints
   {"managedElementRef", "adaptorUri", "protocolSupport", "o1Protocol",
    "entityType", "managedFunctionRef"?, "vendorName", "supportedServices"?}
   → 201 {"endpointId", "managedElementRef", "healthStatus": "DISCOVERED"}
   ```

   Before a capability exists for the vendor, nothing is gated.

3. **Onboard the vendor: discover → load schemas → declare capability, in one call (admin).**

   ```
   POST /ran-nf-oam/vendor-onboarding
   {"vendorName",
    "discoverFrom": "<a managedElementRef registered for this vendor>",
    "conformanceMode": "SPEC" | "OWN" | "COMBINED",
    "schemas": [{"schemaName", "revision", "type", "location", "descriptor"}],
    "supportedServices"?, "supportedVendorModes"?, "schemaRef"?, "specSchemaRef"?}
   → 201 {"vendorName", "discovered", "schemasLoaded", "capability"}
   ```

   - Discovery reads `GET /capabilities` at the registered `adaptorUri`'s origin, through `smo_shared.webhook`. It never fetches a URL from the request. The adaptor answers `{vendorName, supportedServices, supportedVendorModes}`.
   - Values in the body win over discovered ones. `supportedServices` must come from one or the other; vendor modes default to `["O1_NETCONF"]`.
   - `OWN` / `COMBINED` need `schemaRef`; with exactly one entry in `schemas` it defaults to that schema.
   - Errors: discovery ME of another vendor or adaptor declaring another vendor → 422 `SCHEMA_VALIDATION_FAILED`; adaptor unreachable or ME without an adaptor → 503 `ENDPOINT_UNREACHABLE`; a different descriptor at an existing name and revision → 409 `CM_SCHEMA_CONFLICT`; an unknown `schemaRef` → 404 `CM_SCHEMA_NOT_FOUND`; an already-registered endpoint of the vendor using an undeclared mode → 409 `PROTOCOL_NOT_SUPPORTED`.

   The same result is reachable step by step with `POST /cm-schemas` and `PUT /vendor-capabilities/{vendor}`.

4. **Register the vendor's further managed elements** with the same `POST /ran-nf-oam/o1-adaptor-endpoints`. `o1Protocol` must map to a declared vendor mode (`NETCONF` → `O1_NETCONF`, `RESTCONF` → `O1_RESTCONF`; else 409 `PROTOCOL_NOT_SUPPORTED`). An endpoint's `supportedServices` may narrow the vendor's (for example an O-RU exposing only `FM` and `HEARTBEAT`), never widen them (422 `SCHEMA_VALIDATION_FAILED`).

5. **Set cell guards** for cells that rApps must protect: `PUT /ran-nf-oam/managed-entities/{me}/cells/{cell}/guards`.

For a test vendor, `mock-o1-adaptor` serves a configurable `GET /capabilities` (`MOCK_O1_VENDOR_NAME`, `MOCK_O1_SUPPORTED_SERVICES`, `MOCK_O1_VENDOR_MODES`).

### Checks at request time

| Check | Where | Refusal |
|---|---|---|
| Axis-2 presence guard (`require_service`) | `PROV`: `POST /config-jobs`, `GET /managed-entities/{me}/config`; `FM`: `POST /alarms/ingest`, `POST /fm-subscriptions`; `PM`: `POST /pm-subscriptions`, `POST /pm-reports`; `SWM`: `POST /software-management-jobs` | 409 `O1_SERVICE_NOT_SUPPORTED` |
| Axis-3 schema check (`schema_problems`) | every change of `POST /config-jobs`, before a `WriteConfigJob` is created | 422 `SCHEMA_VALIDATION_FAILED`, naming every offending attribute |

- Effective services are the endpoint's own declaration if set, else the vendor's.
- The class of a change comes from `className`, else from the `managedFunctionRef` prefix (`NRCellDU=1` → `NRCellDU`). A change naming no class must use attributes some class defines. Values are checked against the descriptor's `enum` where present (for example `NRCellDU.administrativeState` ∈ {`LOCKED`, `UNLOCKED`}).
- `conformanceMode` selects the descriptor(s): `SPEC` = spec descriptor only; `OWN` = vendor descriptor only; `COMBINED` = spec descriptor plus the vendor descriptor's classes and attributes as named augments.
- A managed element whose vendor has no registered capability skips both checks (permissive default for single-vendor deployments).
- A refused write never reaches the adaptor. DME passes the 4xx back to the rApp and records the action `REJECTED`.

### Limits

- **Transport.** RFC 6241-shaped `edit-config` and RFC 8040 RESTCONF requests, both over plain HTTP (no TLS, auth, notifications or YANG-patch), are dispatched; an ME provisioned for any other protocol is rejected at dispatch with `PROTOCOL_NOT_SUPPORTED`. A new transport needs one client module per transport family, selected by `ManagedEntity.o1_protocol`.
- **YANG.** The ingestion script reads NRM OpenAPI only; a YANG bundle needs a YANG front end (`pyang`) emitting the same descriptor shape.
- **Semantics.** A descriptor documents shape, not runtime behaviour; a vendor that silently ignores an accepted attribute is found only by integration testing against that vendor.

## 3. Unit tests

### 3.1 Running them

```bash
cd smo/ran-nf-oam && PYTHONPATH=.:../shared python -m pytest tests/ -q
```

### 3.2 What is covered

| Test file | Covers | Count |
|---|---|---|
| `tests/test_main.py` | Config dispatch (apply, reject, `operation` threading, RESTCONF dispatch, refusal of a protocol with no client, unreachable / stale / fresh endpoint), `discover` aging, endpoint registration and heartbeat, PM / FM subscription create / list / delete and DME producer registration, `/health` and `/dme-jobs` callbacks, alarm ingest / filter / ack / clear, list reads | 37 |
| `tests/test_vendors.py` | Bundled spec descriptor and custom schema load, capability CRUD and defaults, vendor-mode gating, `SPEC` / `OWN` / `COMBINED` schema checks, unregistered vendor unchecked, service-presence guards, onboarding with discovery and its failures, cell guards | 10 |
| `tests/test_dispatch_reliability.py` | `function-ref` dispatch, retry with backoff, retry exhaustion -> failed change + alarm, no retry on `<rpc-error>`, read-after-write, PM report fan-out to every data job, multi-counter per-relation measurements; RESTCONF retry and alarm, no retry on an `ietf-restconf:errors` reply, RESTCONF read-after-write | 10 |
| `tests/test_netconf_client.py` | RPC builders (`operation`, `function-ref`), `<ok/>` handling, failure reasons, `get-config` parsing | 12 |
| `tests/test_restconf_client.py` | Data-resource URL and key encoding, `yang-data+json` bodies, `operation` -> method mapping (PATCH / PUT / POST on the parent / DELETE), `remove` tolerating `data-missing`, error-reply vs transient failure reasons, GET read-back parsing | 22 |
| `tests/test_statemachine.py` | The three FSMs, aggregation, forbidden transitions (e.g. `ACTIVE` -> `UNREACHABLE`) | 10 |
| **Total** | | **101** |

### 3.3 What is not covered here

- The real DME and mock adaptor round trip (config write end to end, PM -> DME -> rApp, vendor onboarding against `mock-o1-adaptor`): `tests_integration/`.
- Postgres CHECK constraints and FK behaviour (SQLite does not enforce them): `scripts/check_migration_matches_models.py`.
- MSAC role evaluation (not implemented).
- Unknown-id 500 paths on job / alarm lookups are not asserted.

## 4. References

- Call flows: [03 config write with schema check](../docs/call-flows/03-config-write-with-schema-check.md), [19 software management job](../docs/call-flows/19-software-management-job-lifecycle.md), [20 alarm and PM subscription](../docs/call-flows/20-alarm-pm-subscription-lifecycle.md), [21 O1 vendor onboarding](../docs/call-flows/21-o1-vendor-onboarding.md), [14 correlation id](../docs/call-flows/14-correlation-id-propagation.md)
- OpenAPI: [`../docs/openapi/ran-nf-oam.json`](../docs/openapi/ran-nf-oam.json)
- Architecture and R1 conventions: [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md); open work: [`../OPEN_ITEMS.md`](../OPEN_ITEMS.md)
- Specs: [`../../specs/5G_APIs/`](../../specs/5G_APIs/) (TS 28.532 / 28.541 / 28.111 / 28.550), [`../../specs/O1_Adaptor/`](../../specs/O1_Adaptor/)
- Related READMEs: [DME](../dme/README.md), [mock-o1-adaptor](../mock-o1-adaptor/README.md)
