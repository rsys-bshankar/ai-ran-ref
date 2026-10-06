# Onboarding (`onboarding/`)

> Software Package Onboarding SMOS: fetches an rApp package (a TOSCA CSAR), validates it, registers its descriptor with NFO, and owns the package lifecycle and the usage registrations that guard it.

| | |
|---|---|
| Standards basis | O-RAN rApp package onboarding (ASD / TOSCA CSAR, O-RAN-SC rApp Manager) + internal manifest.yaml/capabilities.yaml extension |
| R1 route / port | `/onboarding` via R1 Termination (container `:8000`) |
| Depends on (over R1) | NFO (`POST /nfo/descriptors`); the package location itself (plain HTTP GET, not over R1) |
| Called by | rApp Management (`onboarding-status`, `usage/start`, `usage/stop`); AIMgF (`onboarding-status`, for `aiCapabilities.runtimeProfiles`); GUI BFF (operator and admin actions); operators |
| Database tables | `application_package` (versioned), `artifact`, `package_usage_registration` |
| Unit tests | 104 passed (`tests/`, SQLite, standalone) |
| Status | Done. Package signature verification is not performed (see 1.5, 2.8) |

## 1. High-level design (HLD)

### 1.1 Purpose and scope

Onboarding turns a package location into a deployable, governed package:

1. **Validate.** Fetch the CSAR, check its structure, read its identity and optional AI-platform declarations, register its artifacts.
2. **Register with NFO.** Create the NF deployment descriptor NFO will later instantiate, so rApp Management has a real descriptor id to deploy.
3. **Govern the lifecycle.** `AVAILABLE`, `PRIMED`, `DEPRECATED`, `DELETING`, `FAILED`, with a delete guard that protects anything still in use.
4. **Track usage.** rApp Management registers one usage per instance; open usages block depriming and deletion.

It does not deploy anything (rApp Management and NFO do), does not validate that an rApp works, and does not store the package bytes: the artifacts table records where each file inside the package can be read.

### 1.2 Standards basis

The package format and lifecycle are modelled on the O-RAN-SC rApp Manager (`nonrtric-plt-rappmanager`) and its rApp package (CSAR) with an Application Service Descriptor (ASD).

| Reference | Realised here |
|---|---|
| CSAR layout: `TOSCA-Metadata/TOSCA.meta` with `Entry-Definitions`, `Definitions/`, `Artifacts/` | Same. The entry definitions file is read for identity; every file under `Artifacts/` becomes an `artifact` row |
| ASD node type `tosca.nodes.asd` | `application_name`, `application_version`, `provider` become `name`, `version`, `vendor`; `descriptor_id`, `descriptor_invariant_id`, `descriptor_version`, `schema_version` are stored as given |
| Validator chain: `NamingValidator` | Adopted: a location not ending in `.csar` fails before it is fetched |
| Validator chain: `AsdDescriptorValidator` duplicate check | Adapted: duplicates are detected by SHA-256 of the package bytes (`integrity_hash`), not by `descriptor_id` |
| Validator chain: `FileExistenceValidator` requiring `Files/Acm/definition/compositions.json` | Deliberately not adopted: this build never calls ONAP ACM, so the file is neither required nor read |
| Package priming `COMMISSIONED → PRIMING → PRIMED → DEPRIMING` | `AVAILABLE` plays `COMMISSIONED`. Real resource pre-provisioning behind priming is out of scope, so each step completes inside one request |
| `Files/Sme/providers/*.json`, `Files/Sme/serviceapis/*.json` (CAPIF declarations) | Read at onboarding, stored as `sme_declarations`, registered with SME per instance by rApp Management |

Internal extension (not in any standard): two optional files at the CSAR root, read in addition to the above.

- `capabilities.yaml` declares which of the SDK's six namespaces (data, analytics, models, lifecycle, intent, platform) the rApp consumes or provides.
- `manifest.yaml` declares `executionModes`, `autonomyModes`, `requiredServices` and per-mode `runtimeProfiles` (cpu, memory, gpu), and may declare `limits` (`configJobsPerHour`: CM write jobs the rApp may start in an hour; `maxElementsPerJob`: managed elements one job may touch; `maxChangePercent`: how far a numeric value may move in one write, in percent of its current value; an unknown limit or a bad value fails onboarding). rApp Management puts the limit in force at RAN NF OAM when the instance bootstraps (`AI-10.1`/`10.2`).

The result is stored as `application_package.ai_capabilities` and exposed on the status and package views. A package without either file onboards unchanged. The full layout, every field and what fails onboarding are specified in [`../docs/RAPP_PACKAGING.md`](../docs/RAPP_PACKAGING.md); this README does not repeat it.

### 1.3 Position in the platform

```
 operator / GUI BFF ──POST /packages {location}──▶ Onboarding ──GET location──▶ package server (.csar)
                                                      │  POST /nfo/descriptors
                                                      ▼
                                                     NFO
 rApp Management ──GET onboarding-status, POST usage/start|stop──▶ Onboarding
 AIMgF           ──GET onboarding-status (aiCapabilities.runtimeProfiles)──▶ Onboarding
```

Onboarding never calls rApp Management, AIMgF or SME. The FK from NFO's descriptor table to `application_package` is a cross-module reference declared in the SQL migration, not in the ORM.

### 1.4 Ownership

| Owns | Does not own → owner |
|---|---|
| `ApplicationPackage` and its lifecycle | NF deployment descriptor, deployment placement → NFO |
| Package validation and the CSAR parsing rules | Instances of a package, their state and credentials → rApp Management |
| `Artifact` records | The package bytes (stay at the location) |
| Usage registrations and the cascade-delete / deprime guards | Registering a package's SME declarations → rApp Management, per instance |
| The package's `ai_capabilities` record | Using `runtimeProfiles` to size a runtime → AIMgF; autonomy mode of an instance → rApp Management |

### 1.5 Design decisions

| Decision | Reason |
|---|---|
| `POST /packages` answers `202`, but validation, descriptor creation and the state change all happen inside the request. The response carries the final state implicitly: poll `onboarding-status`. | Keeps the API shape of an async operation without a worker; the pipeline is short. |
| The package row is committed (not just flushed) before NFO is called. | NFO's descriptor row has a real FK on `application_package`; under PostgreSQL's read-committed isolation, NFO's connection cannot see an uncommitted row. |
| Any known validation failure (bad zip, missing entry, unreachable location, NFO not answering 201, duplicate, malformed YAML or JSON, invalid runtime profile) lands the package in `FAILED` with a normal `202`, not an HTTP error. | Failing to validate is an expected outcome of onboarding. |
| `FAILED` is terminal and is deleted directly, skipping the cascade check. | Nothing can depend on a package that never became `AVAILABLE`. |
| A byte-identical package already onboarded is rejected, but one whose earlier package is `DELETING` or `FAILED` does not count. | Identity is the content hash; a deleted package may be onboarded again. |
| `signature_verified` is set to true once validation passes. It records that the package was internally consistent, not that a signature was checked. | Real signature verification is not implemented. |
| Delete from `AVAILABLE` or `DEPRECATED` is blocked while a child package is `AVAILABLE` / `DEPRECATED` or a usage registration has no `stopped_at`; deprime is blocked while a usage registration is open. | Cascade-delete guard; the reference's own deprime guard. |
| `DELETING` is terminal and keeps the row. | Nothing re-enters; the row remains as the record. |
| Instances may be created from `AVAILABLE` or `PRIMED` packages only. Enforced by rApp Management, not here. | Priming is optional. |

Idempotency: usage stop is idempotent (an already stopped registration keeps its first `stoppedAt`). Onboarding the same location twice is refused by the duplicate check, not deduplicated.

Security: the package location is fetched with a direct `httpx.get` (30 s timeout, no redirects). Before the fetch it must end in `.csar` and pass the shared SSRF guard (`smo_shared.webhook.is_safe_webhook_destination`: http/https only; loopback, link-local such as the cloud metadata address, multicast and reserved addresses refused), otherwise the package goes to `FAILED` and nothing is fetched. Any other hostname or private address is allowed, because the package server is usually another container. The caller of `POST /packages` is therefore still trusted to name an appropriate location; the GUI BFF limits this route to the operator role. Onboarding performs no authorization of its own beyond R1 Termination's token check.

## 2. Low-level design (LLD)

### 2.1 Code map

| File | Responsibility |
|---|---|
| `app/main.py` | Routes; `_validate_package` (fetch, zip, TOSCA.meta, identity, artifacts, hash); `_asd_identity`; `_parse_ai_capabilities` and `_validate_runtime_profiles`; `_parse_sme_declarations`; `_create_nf_deployment_descriptor` (NFO call); `_fire` (maps FSM refusals to 409). |
| `app/statemachine.py` | `PackageState`, `PackageEvent`, `ONBOARDING_FSM`, the two guards `_no_blocking_dependents` and `_no_active_instances`. |
| `app/models.py` | The three tables; `PACKAGE_STATES`. |
| `../shared/smo_shared/statemachine.py` | Generic FSM (`fire`, `legal_events`, `IllegalTransition`). |

### 2.2 Data model

**`application_package`**

| Column | Notes |
|---|---|
| `package_id` (PK, UUID) | Also the `trackingId` |
| `application_type` | request value, default `rApp` |
| `name`, `version`, `vendor` | `unresolved-until-validated` / `0.0.0` / null until the ASD is read; stay so if the ASD lacks the fields |
| `state` | see 2.3 |
| `parent_package_id` (FK to itself, null) | read by the delete guard; no route sets it |
| `manifest_ref` | the location the package was fetched from |
| `tosca_entry_definitions` | the `Entry-Definitions` path |
| `signature_verified` | see 1.5 |
| `integrity_hash` | SHA-256 of the package bytes; the identity used for duplicate detection |
| `descriptor_id`, `descriptor_invariant_id`, `descriptor_version`, `schema_version` | ASD fields, surfaced, not used as a key |
| `sme_declarations` (JSON, null) | `{providers: [...], serviceApis: [...]}` from `Files/Sme/` |
| `ai_capabilities` (JSON, null) | `manifestVersion`, `aiRuntimeSdkVersion`, `executionModes`, `autonomyModes`, `requiredServices`, `runtimeProfiles`, `consumes`, `provides` (only those present) |
| `nf_deployment_descriptor_id` (null) | bare UUID into NFO's descriptor table; the FK exists only in the SQL migration |

**`artifact`**: `artifact_id` (PK), `package_id` (FK, cascade), `path` (inside the CSAR), `access_url` (`<location>#<path>`).

**`package_usage_registration`**: `id` (PK), `package_id` (FK), `consumer_id` (the rApp instance id), `stopped_at` (null while in use).

### 2.3 State machines

`PackageState`: `ONBOARDING`, `AVAILABLE`, `PRIMING`, `PRIMED`, `DEPRIMING`, `DEPRECATED`, `DELETING`, `FAILED`.

| From | Event | To | Guard / note |
|---|---|---|---|
| `ONBOARDING` | `VALIDATE_OK` | `AVAILABLE` | fired by the onboarding pipeline |
| `ONBOARDING` | `VALIDATE_FAILED` | `FAILED` | any known validation failure |
| `AVAILABLE` | `PRIME` | `PRIMING` | |
| `PRIMING` | `PRIME_COMPLETE` | `PRIMED` | fired in the same request as `PRIME` |
| `PRIMED` | `DEPRIME` | `DEPRIMING` | guard `_no_active_instances`: no usage registration without `stopped_at` |
| `DEPRIMING` | `DEPRIME_COMPLETE` | `AVAILABLE` | fired in the same request as `DEPRIME` |
| `AVAILABLE` | `DEPRECATE` | `DEPRECATED` | |
| `DEPRECATED` | `CANCEL_DELETE` | `AVAILABLE` | |
| `AVAILABLE`, `DEPRECATED` | `DELETE` | `DELETING` | guard `_no_blocking_dependents`: no child in `AVAILABLE` / `DEPRECATED`, no open usage |
| `FAILED` | (direct delete) | row removed | handled by the route, not the FSM |

`PRIMING` and `DEPRIMING` are never observable from outside. Forbidden: everything not in the table, for example `PRIMED → DEPRECATE` or `PRIMED → DELETE` (deprime first), `DEPRECATED → PRIME`, `AVAILABLE → DEPRIME`, any event from `FAILED` through the FSM, any event from `DELETING`. `models.PACKAGE_STATES` lists only five of the eight states and is not used by any code.

Event routes and refusals: an event with no edge from the current state gives `409 LIFECYCLE_ILLEGAL_TRANSITION` naming the state and the event. An event whose edge exists but whose guard refuses gives `409 SERVICE_NAME_CONFLICT` with the reason.

### 2.4 API

| Method | Path | Purpose | Notable errors |
|---|---|---|---|
| POST | `/packages` (202) | `{location, applicationType="rApp"}` → `{packageId, trackingId}`. Runs the validation pipeline. | none for a validation failure (the package is `FAILED`) |
| GET | `/packages?state=` | Paged list of package views | |
| GET | `/packages/{id}/onboarding-status` | `{packageId, state, nfDeploymentDescriptorId, smeDeclarations, aiCapabilities}`; the read rApp Management and AIMgF depend on | 404 `PACKAGE_NOT_FOUND` |
| GET | `/packages/{id}/artifacts` | Paged `{artifactId, path, accessUrl}` | 404 |
| GET | `/packages/{id}/usage` | Paged `{registrationId, consumerId, stoppedAt, active}`: what blocks a delete | 404 |
| POST | `/packages/{id}/prime` | `AVAILABLE → PRIMED` | 409 `LIFECYCLE_ILLEGAL_TRANSITION` |
| POST | `/packages/{id}/deprime` | `PRIMED → AVAILABLE` | 409 `SERVICE_NAME_CONFLICT` (open usage); 409 `LIFECYCLE_ILLEGAL_TRANSITION` |
| POST | `/packages/{id}/deprecate` | `AVAILABLE → DEPRECATED` | 409 `LIFECYCLE_ILLEGAL_TRANSITION` |
| POST | `/packages/{id}/cancel-delete` | `DEPRECATED → AVAILABLE` | 409 `LIFECYCLE_ILLEGAL_TRANSITION` |
| DELETE | `/packages/{id}` | `AVAILABLE` / `DEPRECATED → DELETING` (returns the view); `FAILED` → row deleted, `{"status": "deleted"}` | 409 `SERVICE_NAME_CONFLICT` (child or open usage); 409 `LIFECYCLE_ILLEGAL_TRANSITION` (any other state) |
| POST | `/packages/{id}/usage/start?consumer_id=` | Open a usage → `{registrationId}` | 404 |
| POST | `/packages/{id}/usage/{registration_id}/stop` | Close it (idempotent) → `{status: stopped}` | 404 `PACKAGE_NOT_FOUND`; 404 `PACKAGE_USAGE_REGISTRATION_NOT_FOUND` (unknown, or belongs to another package) |

Package view: `packageId, name, version, vendor, applicationType, state, toscaEntryDefinitions, descriptorId, descriptorInvariantId, descriptorVersion, schemaVersion, signatureVerified, nfDeploymentDescriptorId, aiCapabilities, smeDeclarations`. `GET /health` is liveness.

### 2.5 Interactions

The onboarding pipeline, in order, inside `POST /packages`:

| Step | Action | On failure |
|---|---|---|
| 1 | Insert the row in `ONBOARDING`, commit | |
| 2 | Reject a location not ending in `.csar` | `FAILED` |
| 3 | `GET {location}` (30 s), open as zip, read `TOSCA-Metadata/TOSCA.meta` and `Entry-Definitions`, read that file, scan it line by line for the ASD identity keys | `FAILED` (unreachable, bad zip, missing file) |
| 4 | Collect `Artifacts/*`; parse `manifest.yaml` / `capabilities.yaml` (YAML) and validate `runtimeProfiles`; collect `Files/Sme/providers/*.json` and `serviceapis/*.json` | `FAILED` (malformed YAML or JSON, invalid profile) |
| 5 | Duplicate check on the SHA-256 against packages not `DELETING` / `FAILED` | `FAILED` |
| 6 | `POST /nfo/descriptors` `{packageId, name: <entry definitions>, workloadTemplate: {toscaEntryDefinitions}}`; needs `201` and `nfDeploymentDescriptorId` | `FAILED` (any other status) |
| 7 | Fire `VALIDATE_OK`, commit | |

A `FAILED` package keeps whatever identity fields and artifact rows steps 3 to 5 had already set. The descriptor's `workloadTemplate` is a thin reference to the entry definitions, not a parsed TOSCA node template. An NFO transport failure (as opposed to a non-201 reply) is an `httpx` error and is also treated as a validation failure.

Inbound: rApp Management calls `onboarding-status` on every create, upgrade and bootstrap-complete, and `usage/start` / `usage/stop` around an instance's life; AIMgF reads `aiCapabilities.runtimeProfiles` before it sizes a runtime. There are no callbacks and no background tasks.

### 2.6 Configuration

Onboarding reads no environment variable of its own. Through `smo_shared`: `SMO_DATABASE_URL` (required, no default) and `R1_GATEWAY_URL` (default `http://r1-termination:8000`, for the NFO call). Constant: package fetch timeout 30 s.

**Metrics (PR-OBS-4).** Besides the shared series, `GET /metrics` has `smo_rapp_packages{state}`: the `application_package` rows by `PackageState` (every state present, 0 when empty; `AVAILABLE`, `PRIMED` and so on are the rApps onboarded), read from the database at scrape time (cached 15 s). Aggregate replicas with `max`.

### 2.7 Error codes

| Code | Status | When |
|---|---|---|
| `PACKAGE_NOT_FOUND` | 404 | Unknown package on any `/packages/{id}` route |
| `PACKAGE_USAGE_REGISTRATION_NOT_FOUND` | 404 | Usage stop for an unknown or foreign registration |
| `LIFECYCLE_ILLEGAL_TRANSITION` | 409 | The lifecycle event has no edge from the package's current state |
| `SERVICE_NAME_CONFLICT` | 409 | The edge exists but its guard refused: open usage (deprime, delete) or a dependent child (delete). The code name is borrowed from SME; the detail says what blocked it |

The reasons a package becomes `FAILED` are not HTTP errors; they are listed in 2.5 and, in full, in `RAPP_PACKAGING.md`.

### 2.8 Limits and open items

- No signature verification (`signature_verified` means "validated", see 1.5).
- Package location is fetched as given (suffix check only); no allowlist, no size limit.
- Priming performs no resource pre-provisioning; `PRIMING` and `DEPRIMING` are instantaneous.
- `parent_package_id` is read by the delete guard but nothing sets it, so the child-package guard is only reachable through direct database writes.
- ASD identity is a line scan of flat `key: value` lines, not a YAML parse.
- Package bytes are not stored; `access_url` points back into the original location.
- `models.PACKAGE_STATES` is stale (see 2.3).
- No open item in [`../OPEN_ITEMS.md`](../OPEN_ITEMS.md) names this module.

## 3. Unit tests

### 3.1 Running them

```bash
cd smo/onboarding && PYTHONPATH=.:../shared python -m pytest tests/ -q
```

### 3.2 What is covered

| Test file | Covers | Count |
|---|---|---|
| `tests/test_main.py` | Onboarding success (descriptor created via NFO, no ACM file needed, identity and ASD descriptor fields resolved, placeholder kept when absent) | 6 |
| | Onboarding failures to `FAILED` (NFO refuses, malformed zip, not `.csar`, malformed capabilities YAML, malformed SME JSON, byte-identical duplicate) | 6 |
| | `manifest.yaml` / `capabilities.yaml`: null when absent, parsed when present, capabilities alone, execution modes and runtime profiles, four invalid-profile cases | 8 |
| | SME declarations: null when absent, parsed | 2 |
| | Re-onboarding after delete / failure, live packages still block duplicates | 6 |
| | Lifecycle: deprecate / cancel-delete, delete (failed, plain, blocked by child, blocked by usage, allowed after stop), prime, deprime (plain, blocked, allowed after stop) | 10 |
| | Illegal events are 409 naming state and event; delete without an edge | 9 |
| | Unknown package / registration is 404 on every route | 10 |
| | Usage: stop idempotent, listing shows what blocks a delete | 2 |
| | Package list and state filter, identity fields for the GUI, health | 3 |
| | Package row committed before the NFO call | 1 |
| `tests/test_statemachine.py` | The FSM alone: success and failure onboarding, deprecate round trip, delete guards (child, usage, after stop), no transition from `FAILED`, prime / deprime round trip and guard, no `ONBOARDING → PRIME`, no `PRIMED → DELETE` | 11 |
| `tests/test_business_metrics.py` | `smo_rapp_packages` counts packages by state with every state of `PackageState` present | 1 |
| | Total | 75 |


### 3.3 What is not covered here

- A real CSAR from `samples/` onboarding and deploying through rApp Management and NFO: `tests_integration/test_cross_service.py` (`test_real_demo_csar_onboards_and_deploys`, `test_onboarding_to_rapp_management_full_deploy_creates_real_nf_deployment_descriptor`, `test_runtime_profile_flows_from_rapp_manifest_to_nfo_descriptor`). The same suite fails if a committed `.csar` no longer matches its sources.
- The FK between NFO's descriptor table and `application_package` on PostgreSQL: `tests_integration/` and `scripts/check_migration_matches_models.py`.
- Real network fetch of a package location: unit tests substitute the HTTP call.

## 4. References

- Packaging specification (layout, manifest and capabilities fields, failure reasons): [`../docs/RAPP_PACKAGING.md`](../docs/RAPP_PACKAGING.md)
- Call flows: [01 onboarding to deployment](../docs/call-flows/01-rapp-onboarding-to-deployment.md), [06 package lifecycle](../docs/call-flows/06-package-lifecycle.md)
- OpenAPI: [`../docs/openapi/onboarding.json`](../docs/openapi/onboarding.json)
- Cross-cutting rules: [ARCHITECTURE.md](../docs/ARCHITECTURE.md); history: [`../HISTORY.md`](../HISTORY.md)
- Related READMEs: [rApp Management](../rapp-mgmt/README.md), [SME](../sme/README.md), [R1 Termination](../r1-termination/README.md)
