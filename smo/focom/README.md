# FOCOM (`focom/`)

> The SMO's O2-IMS side: the O-Cloud infrastructure inventory (resource types, pools, deployment managers, resources), inventory-change subscriptions, infrastructure alarms and performance, and a TEIV-shaped topology export.

| | |
|---|---|
| Standards basis | O-RAN O2-IMS (FOCOM) |
| R1 route / port | `/focom` via R1 Termination (container :8000) |
| Depends on (over R1) | none (inventory-change callbacks go to subscriber URLs via `smo_shared.webhook`) |
| Called by | NFO (`GET /focom/inventory`, to resolve `oCloudId`), SO SMOS (`POST /focom/resources/provision`), GUI / GUI BFF |
| Database tables | `inventory_subscription`, `resource_type`, `resource_pool`, `resource`, `deployment_manager`, `ocloud_alarm`, `ocloud_performance_metric` |
| Unit tests | 50 passed (`tests/`, SQLite, standalone) |
| Status | Done for the Phase 1 single-cluster scope. Open: `SA-FOCOM-2`, `SA-FOCOM-6`, `SA-FOCOM-7`, `SA-FOCOM-9` (see [section 2.8](#28-limits-and-open-items)) |

## 1. High-level design (HLD)

### 1.1 Purpose and scope

FOCOM is the platform's view of the infrastructure that workloads run on, as an O2-IMS inventory. NFO asks it where to place a workload; operators and the GUI read it; other consumers subscribe to inventory changes. Its alarm and performance domain is infrastructure (O-Cloud host / node / cluster health), explicitly distinct from RAN NF OAM's RAN-function alarms.

Phase 1 topology is one degenerate O-Cloud: one cluster (`phase1-degenerate-cluster`), one resource type (`generic`), one pool (`pool-0`) and one deployment manager (`dm-0`), seeded lazily on first read.

### 1.2 Standards basis

| Spec | What is realised | What is deliberately not |
|---|---|---|
| O-RAN WG6 O2-IMS information model ([`../../specs/o-cloud-im/resources/ORAN.O2ims.Inventory.yaml`](../../specs/o-cloud-im/resources/ORAN.O2ims.Inventory.yaml), [`Common`](../../specs/o-cloud-im/resources/ORAN.O2ims.Common.yaml)) | `OCloud` aggregate (`GET /inventory`: `oCloudId`, `name`, `description`, `resourceTypes`, `deploymentManagers`); `ResourceType` (vendor, model, version, dictionary ids, `resourceKind`, `resourceClass`, `extensions`); `ResourcePool`; `Resource` (`globalAssetId`, `tags`, `groups`, parent / child); `DeploymentManager` (`supportedLocations`, `capabilities`, `capacity`); `InventorySubscription` with `callback` and `consumerSubscriptionId` and typed notifications | `locations` / `oCloudSites` are returned empty (the spec requires at least one; FOCOM has no `OCloudSite` / `Location`); `globalCloudId`, `infrastructureManagementServicesEndPoint`, `smoRegistrationService` are null (`SA-FOCOM-2`). No ProvisioningRequest, Artifacts, NodeCluster or Infrastructure resources ([`Provisioning`](../../specs/o-cloud-im/resources/ORAN.O2ims.Provisioning.yaml), [`Artifacts`](../../specs/o-cloud-im/resources/ORAN.O2ims.Artifacts.yaml), [`Cluster`](../../specs/o-cloud-im/resources/ORAN.O2ims.Cluster.yaml), [`Infrastructure`](../../specs/o-cloud-im/resources/ORAN.O2ims.Infrastructure.yaml)); single-cluster scope (`SA-FOCOM-7`) |
| O2-IMS alarms / performance | A flat three-field `OCloudAlarm` and a metric record, read-only for performance | No `AlarmEventRecord` with an X.733 `eventType`, no alarm subscription / notify, no performance ingest (`SA-FOCOM-6`) |
| O-RAN-SC `focom-to-teiv-adapter` (wire shape) | `GET /topology` exports entities and relationships in the adapter's `o-ran-smo-teiv-cloud:*` shape, pull-based | The adapter's Kubernetes CRD reads, kubeconfig access and Kafka CloudEvents push are out of scope |

The route names (`/resource-types`, `/resource-pools`, `/deployment-managers`, `/inventory`) follow the O2-IMS collection names in kebab-case; the O2-IMS spec files were audited against the O-RAN-SC `pti-o2` reference ([specs README](../../specs/README.md)).

### 1.3 Position in the platform

```
 NFO ----R1----> FOCOM  GET /inventory            SO SMOS --R1--> FOCOM  POST /resources/provision
 GUI  ---R1----> FOCOM  reads, subscriptions
 FOCOM --webhook--> subscriber callback (inventory CREATE / DELETE events)
```

- FOCOM calls no other module. It is called by NFO (placement) and SO SMOS (provisioning), and read by the GUI.
- It owns infrastructure alarms (`OCloudAlarm`); RAN-function alarms are RAN NF OAM's.

### 1.4 Ownership

| Owns | Does not own → owner |
|---|---|
| O-Cloud inventory: `ResourceType`, `ResourcePool`, `Resource`, `DeploymentManager` | Workload placement decision, deployments and their lifecycle → NFO |
| `InventorySubscription` and change notification | RAN-function alarms, PM, CM → RAN NF OAM |
| `OCloudAlarm`, `OCloudPerformanceMetric` (infrastructure domain) | Model / runtime lifecycle → AIMgF |
| TEIV-shaped topology export | A topology store (TEIV itself) → external |

### 1.5 Design decisions

- **Lazy seeding.** The single Phase 1 topology is inserted on the first read (`_ensure_phase1_topology`), not at startup, so no module needs a startup seeding step.
- **No fabricated data.** `locations` and `oCloudSites` are empty rather than invented; spec-required fields FOCOM cannot know are null.
- **`oCloudId` is the placement contract.** NFO reads `oCloudId` (not the filtered `resourceTypes`); `resource_type` filters `resourceTypes` only.
- **Spec field names where a spec fixes them.** The subscription callback is `callback` (O2-IMS), not `notificationDestination`; `consumerSubscriptionId` is stored and echoed on every notification.
- **Best-effort notification.** Delivery through `smo_shared.webhook` with a 2 s timeout; an unreachable subscriber never fails provisioning.
- **Permissive provisioning.** `POST /resources/provision` auto-registers an unknown `resourceTypeId` as a `ResourceType` rather than rejecting it (O2-IMS `ResourceType` is read-only; `SA-FOCOM-9`). Deprovisioning an unknown or non-UUID id is a successful no-op and still notifies.
- **Security.** No in-module authorization; R1 Termination introspects tokens. GUI BFF: provision, deprovision and alarm ingest are admin; inventory subscriptions are operator.

## 2. Low-level design (LLD)

### 2.1 Code map

| File | Responsibility |
|---|---|
| `app/main.py` | Routes, lazy topology seeding, inventory-change notification, TEIV topology export |
| `app/models.py` | SQLAlchemy models |

### 2.2 Data model

String identifiers for the Phase 1 concepts, UUIDs for provisioned resources.

| Table | Key columns | Notes |
|---|---|---|
| `resource_type` | PK `resource_type_id` (string); `name`, `description`, `vendor`, `model`, `version`, `alarm_dictionary_id`, `performance_dictionary_id`, `resource_kind`, `resource_class`, `extensions` | Seeded `generic`; others auto-registered by provisioning |
| `resource_pool` | PK `resource_pool_id`; `name`, `description`, `o_cloud_id` | Seeded `pool-0` |
| `deployment_manager` | PK `deployment_manager_id`; `name`, `description`, `o_cloud_id`, `service_uri`, `supported_locations`, `capabilities`, `capacity` | Seeded `dm-0` (name = cluster id, `service_uri http://phase1-degenerate-cluster:6443`) |
| `resource` | PK `resource_id` (UUID); FK `resource_type_id`, FK `resource_pool_id`; `parent_id` (UUID, not an FK), `description`, `global_asset_id`, `tags`, `groups` | Always created in `pool-0` |
| `inventory_subscription` | PK `subscription_id`; `callback`, `consumer_subscription_id`, `resource_type_id` (optional filter) | |
| `ocloud_alarm` | PK `alarm_id`; `resource_ref`, `severity`, `raised_at` | |
| `ocloud_performance_metric` | PK `id`; `resource_ref`, `metric_name`, `value`, `collected_at` | No route writes it |

### 2.3 State machines

None: stateless. Resources are created and deleted; there is no lifecycle state. `GET /resources/{id}/status` always returns `healthy`.

### 2.4 API

All routes are under `/focom` through R1. Lists return `{items, total, limit, offset}`.

**Inventory and topology**

| Method | Path | Purpose / notable errors |
|---|---|---|
| GET | `/inventory` | The `OCloud` object; optional `resource_type` filters `resourceTypes` (unregistered type -> empty list, not an error) |
| GET | `/resource-types`, `/resource-types/{id}` | 404 `RESOURCE_TYPE_NOT_FOUND` |
| GET | `/resource-pools`, `/resource-pools/{id}` | 404 `RESOURCE_POOL_NOT_FOUND` |
| GET | `/resource-pools/{id}/resources` | Resources of a pool; 404 `RESOURCE_POOL_NOT_FOUND` |
| GET | `/deployment-managers`, `/deployment-managers/{id}` | 404 `DEPLOYMENT_MANAGER_NOT_FOUND` |
| GET | `/topology` | TEIV-shaped export: `entities` (`ResourceType`, `ResourcePool`, `DeploymentManager`, `Resource`, keyed `o-ran-smo-teiv-cloud:<Type>`, each `{id, attributes}`) and `relationships` (`RESOURCE_IS_OF_TYPE_RESOURCETYPE`, `RESOURCE_CONTAINED_IN_RESOURCEPOOL`, `RESOURCE_CHILD_OF_RESOURCE`, each `{id, aSide, bSide, sourceIds}`); ids are `urn:oran:smo:teiv:<Type>:<id>` |

**Provisioning and monitoring**

| Method | Path | Purpose |
|---|---|---|
| POST | `/resources/provision` | Body is a free dict: `resourceTypeId` (default `generic`), `description`, `globalAssetId`, `tags`, `groups`. Creates a `Resource` in `pool-0`; returns `{resourceId, clusterId}`; notifies `CREATE` |
| DELETE | `/resources/{resource_id}` | Returns `{status: "deprovisioned"}` (200); unknown or non-UUID id is a no-op; notifies `DELETE` |
| GET | `/resources/{resource_id}/status` | Always `{resourceId, status: "healthy"}` |

**Subscriptions**

| Method | Path | Purpose |
|---|---|---|
| POST | `/inventory/subscriptions` | Body `callback`, `resourceTypeId?`, `consumerSubscriptionId?`; 201 `{subscriptionId, consumerSubscriptionId}` |
| GET | `/inventory/subscriptions` | List |
| DELETE | `/inventory/subscriptions/{subscription_id}` | 204, idempotent |

**Infrastructure alarms and performance**

| Method | Path | Purpose |
|---|---|---|
| POST | `/alarms/ingest` | Query `resource_ref`, `severity`; returns `{alarmId}` |
| GET | `/alarms` | List (`alarmId`, `resourceRef`, `severity`) |
| GET | `/performance` | List; filter `resource_ref` (`resourceRef`, `metricName`, `value`) |

**Liveness**: `GET /health` (GUI BFF module-status fan-out).

### 2.5 Interactions

- **Inventory-change notification.** On provision (`CREATE`) and deprovision (`DELETE`), every subscription is considered; one with a `resourceTypeId` filter is skipped when it differs from the resource's type (an unknown type, as when deprovisioning an unprovisioned id, matches every filter). Delivery: `POST <callback>` with `{objectType: "resource", notificationEventType, resourceId, resourceTypeId, consumerSubscriptionId}` via `smo_shared.webhook.post_webhook`, 2 s timeout, failures swallowed.
- **Outbound R1 calls:** none. **Background tasks:** none.
- **Inbound:** NFO `GET /inventory` at every Instantiate; SO SMOS provisioning ([call flow 16](../docs/call-flows/16-focom-resource-inventory-lifecycle.md)).

### 2.6 Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SMO_DATABASE_URL` | `postgresql+psycopg://smo:smo@postgres:5432/smo` | Database (shared lib) |

No FOCOM-specific variables. Constants: cluster id `phase1-degenerate-cluster`, type `generic`, pool `pool-0`, deployment manager `dm-0`.

### 2.7 Error codes

Returned as `{"detail": {"type": "about:blank", "title": <code>, "status", "detail"}}` (code = `title`).

| Code | HTTP | When |
|---|---|---|
| `RESOURCE_TYPE_NOT_FOUND` | 404 | `GET /resource-types/{id}` unknown |
| `RESOURCE_POOL_NOT_FOUND` | 404 | `GET /resource-pools/{id}` or its `/resources` unknown |
| `DEPLOYMENT_MANAGER_NOT_FOUND` | 404 | `GET /deployment-managers/{id}` unknown |

### 2.8 Limits and open items

- **Sites and locations.** No `OCloudSite` / `Location`; `GET /inventory` returns empty `locations` / `oCloudSites`; pools carry no `oCloudSiteId` and no inline `resources` (`SA-FOCOM-2`).
- **Alarms and performance.** Flat three-field alarm, no subscribe / notify, no performance ingest route (`SA-FOCOM-6`).
- **Provisioning model.** No ProvisioningRequest, Artifacts, NodeCluster or Infrastructure (`SA-FOCOM-7`); provisioning auto-registers unknown resource types (`SA-FOCOM-9`); `provision` takes an untyped dict.
- **Stubbed health.** `GET /resources/{id}/status` is a constant; no hardware telemetry populates the resource tree (only the `parent_id` shape exists).
- **Pool.** Resources are always created in `pool-0`.
- **No topology push.** `/topology` is a pull; no Kafka / CRD integration.

## 3. Unit tests

### 3.1 Running them

```bash
cd smo/focom && PYTHONPATH=.:../shared python -m pytest tests/ -q
```

### 3.2 What is covered

| Test file | Covers | Count |
|---|---|---|
| `tests/test_main.py` | `/inventory` shape, seeded type / manager, no fabricated locations, `resource_type` filtering; subscriptions (create, `consumerSubscriptionId`, delete idempotent, list); notifications on provision / deprovision (match, filtered out, delete regardless of filter, real type on delete, unreachable subscriber, id pass-through); provision / deprovision (incl. unknown id, new fields, auto-registered type); resource types, pools, pool resources, deployment managers (list, get, 404, spec-field exposure); alarms and performance (ingest, filter, empty); `/topology` entities and relationships; `/resources/{id}/status`; `/health` | 50 |

Subscriber callbacks are intercepted by patching `httpx.post`.

### 3.3 What is not covered here

- NFO resolving a cluster from a real FOCOM, SO SMOS provisioning: `tests_integration/`.
- Foreign-key behaviour when auto-registering a type and provisioning in one request (found against Postgres; SQLite does not enforce it): Postgres verification via `scripts/check_migration_matches_models.py`.
- Anything in the open items above.

## 4. References

- Call flows: [16 FOCOM resource inventory lifecycle](../docs/call-flows/16-focom-resource-inventory-lifecycle.md), [15 NFO workload lifecycle](../docs/call-flows/15-nfo-workload-lifecycle.md)
- OpenAPI: [`../docs/openapi/focom.json`](../docs/openapi/focom.json)
- Architecture and R1 conventions: [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md); open work: [`../OPEN_ITEMS.md`](../OPEN_ITEMS.md)
- Specs: [`../../specs/o-cloud-im/`](../../specs/o-cloud-im/), [`../../specs/README.md`](../../specs/README.md)
- Related READMEs: [NFO](../nfo/README.md)
