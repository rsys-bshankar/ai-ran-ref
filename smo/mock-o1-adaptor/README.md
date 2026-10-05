# Mock O1 Adaptor (`mock-o1-adaptor/`)

> An in-memory NETCONF- and RESTCONF-shaped O1 adaptor test double: it answers RAN NF OAM's `edit-config` / `get-config` over HTTP and the same operations as RFC 8040 requests under `/restconf`, declares a configurable vendor capability, and offers test-only fault injection and introspection.

| | |
|---|---|
| Standards basis | Test double of an O1 NETCONF / RESTCONF adaptor (not a standard component) |
| R1 route / port | None: not R1-facing and not routed by R1 Termination. Container :8000 on the default compose network (no isolated segment); a managed element's `adaptor_uri` points at `http://mock-o1-adaptor:8000/edit-config` (NETCONF) or the RESTCONF root `http://mock-o1-adaptor:8000/restconf` |
| Depends on (over R1) | none |
| Called by | RAN NF OAM (`POST /edit-config` for both `edit-config` and `get-config`, `/restconf/data/...` for a RESTCONF ME, `GET /capabilities` for vendor discovery); tests and demos (introspection and fault routes) |
| Database tables | none (in-memory dicts) |
| Unit tests | 25 passed (`tests/`, standalone) |
| Status | Done (NETCONF and RESTCONF, `OI-1-cm-sync-restconf`). Deliberately not a NETCONF / RESTCONF / YANG implementation (see [section 2.8](#28-limits-and-open-items)) |

## 1. High-level design (HLD)

### 1.1 Purpose and scope

RAN NF OAM dispatches CM writes as RFC 6241 `<edit-config>` RPCs, or as RFC 8040 RESTCONF requests for an ME provisioned `RESTCONF`, to a managed element's registered adaptor URI and reads back with `<get-config>` or a RESTCONF GET. This service is the thing on the other end, so the real HTTP round trip, the retry and rollback paths, the read-after-write check and the vendor-onboarding discovery can all be exercised without a network simulator.

It is a test and demo double, not an SMO module, and does just enough NETCONF-shaped XML and RESTCONF-shaped JSON handling to close that loop. It can stand in for several vendors by configuration.

### 1.2 Standards basis

| Spec | What is realised | What is deliberately not |
|---|---|---|
| RFC 6241 NETCONF (message shape only) | `<rpc><edit-config>` with a `<managed-object ref= function-ref= operation=>` payload and `<rpc-reply><ok/>` / `<rpc-error>`; the `operation` attribute (`merge` default, `delete`, `remove`); `<get-config>` with a `<filter>` and `<data>` reply | No SSH / NETCONF session, no capability exchange, no YANG validation, no datastores other than "running", no locking or commit. The XML is this project's simplified `managed-object` form, over plain HTTP |
| RFC 8040 RESTCONF / RFC 7951 JSON (message shape only) | RESTCONF root `/restconf` with `/.well-known/host-meta` discovery; data resource `/restconf/data/managed-element={ref}[/managed-function={function-ref}]` (percent-encoded keys); `GET`, `PATCH` (merge), `PUT` (replace; 201 if new, else 204), `DELETE`, and `POST` on `/restconf/data` or a managed element to create a child (201, 409 `data-exists` if present); `application/yang-data+json` list-entry bodies; `ietf-restconf:errors` replies | No TLS / auth, no YANG validation, no query parameters, notifications or YANG-patch, no datastores other than running. Only the managed-element / managed-function resources are modelled |
| 3GPP TS 28.541 (default values only) | Per-IOC default attribute values (both protocols) for a handful of IOCs (see 2.2); `energySavingState` follows `energySavingControl` (CESManagementFunction) | No NRM modelling or value-range checking |
| O1 vendor capability declaration (internal, [RAN NF OAM](../ran-nf-oam/README.md)) | `GET /capabilities` returns `{vendorName, supportedServices, supportedVendorModes}` | n/a |

The real O-RAN-SC `sim-o1-interface` (`ntsim-ng`) is a YANG-validated NETCONF / SSH network simulator and is out of proportion with this single-stack build; only its role is substituted.

### 1.3 Position in the platform

```
 RAN NF OAM --HTTP XML (edit-config / get-config)--> mock-o1-adaptor
 RAN NF OAM --HTTP JSON (PATCH / PUT / POST / DELETE / GET /restconf/data/...)--> mock-o1-adaptor   (RESTCONF ME)
 RAN NF OAM --HTTP GET /capabilities---------------> mock-o1-adaptor   (vendor onboarding discovery)
 tests / demo --HTTP GET /objects, /edit-config/{ref}, POST /faults, DELETE /state --> mock-o1-adaptor
```

- RAN NF OAM is the only product caller; it addresses the mock through the `adaptor_uri` in its own endpoint registry. There is no isolated network segment: no design requirement calls for one.
- The mock calls nothing and has no database.

### 1.4 Ownership

| Owns | Does not own → owner |
|---|---|
| The simulated running configuration per managed function (process memory) | The endpoint registry, vendor capability registry, CM schema checks, retries → RAN NF OAM |
| Test fault queue | Which changes are legal for a vendor → RAN NF OAM (the mock does not validate attributes) |
| The configurable vendor declaration | Real device behaviour → a real vendor adaptor |

### 1.5 Design decisions

- **Rejection is an RPC answer.** Every refusal is a 200 `<rpc-reply>` containing `<rpc-error>` (a real agent refusing a request); only the `TIMEOUT` fault is an HTTP status (504).
- **Delete needs no payload.** An empty body is rejected (`invalid-value`) for every operation except `delete` / `remove`, which legitimately carry no attributes (RFC 6241 section 7.2).
- **Safe XML.** The request body is parsed with `defusedxml`; entity-expansion or external-entity XML is answered `malformed-message` exactly like unparseable XML. Echoed values in `get-config` replies are escaped.
- **Config model is merge-and-default.** Writes merge into the object's state; a never-written function reads as its IOC's defaults. An unknown IOC has no defaults (empty).
- **Faults are consumed.** An injected fault applies to the next `count` matching `edit-config` calls, then disappears; this makes retry sequences deterministic.
- **`IGNORE_WRITE` models a lying agent**: `<ok/>` is returned but nothing is applied, so read-after-write verification can be tested.
- **One image, many vendors.** The capability declaration comes from environment variables.

## 2. Low-level design (LLD)

### 2.1 Code map

| File | Responsibility |
|---|---|
| `app/main.py` | The whole service: RPC handling, the RESTCONF routes over the same state, defaults, capability declaration, fault and introspection routes |

### 2.2 Data model

None: in-memory (module-level dicts).

| Store | Shape |
|---|---|
| `_applied_changes` | managed-object `ref` -> the attribute changes of the last applied `edit-config` (not per function) |
| `_object_state` | `(ref, function_ref)` -> merged running attributes |
| `_faults` | list of `{mode, count, managedObjectRef?}` |

`IOC_DEFAULTS`, keyed by the `function-ref` prefix before `=`:

| IOC | Defaults |
|---|---|
| `NRCellDU` | `administrativeState` UNLOCKED, `operationalState` ENABLED |
| `CESManagementFunction` | `energySavingControl` TO_BE_NOT_ENERGY_SAVING, `energySavingState` IS_NOT_ENERGY_SAVING |
| `NRCellRelation` | `cellIndividualOffset` `[0, 0, 0, 0, 0, 0]`, `isHOAllowed` true, `isMLBAllowed` true |
| `DMROFunction` | `dmroControl` true, `maximumDeviationHoTriggerLow` -12, `maximumDeviationHoTriggerHigh` 12, `minimumTimeBetweenHoTriggerChange` 10, `tstoreUEcntxt` 100 |
| `CommonBeamformingFunction` | `digitalTilt` 60 (tenths of a degree, positive = downtilt), `digitalAzimuth` 0, `coverageShape` 0 |
| `NRSectorCarrier` | `configuredMaxTxPower` 43 (dBm in this build), `txDirection` DL_AND_UL |
| `NRFreqRelation` | `cellReselectionPriority` 5, `qOffsetFreq` 0 |

### 2.3 State machines

None: stateless with respect to lifecycle. Setting `energySavingControl` also sets `energySavingState` (`TO_BE_ENERGY_SAVING` -> `IS_ENERGY_SAVING`, anything else -> `IS_NOT_ENERGY_SAVING`).

### 2.4 API

Not R1-facing. The OpenAPI document is [`../docs/openapi/mock-o1-adaptor.json`](../docs/openapi/mock-o1-adaptor.json) (9 paths).

**NETCONF-shaped surface (used by RAN NF OAM)**

| Method | Path | Purpose / responses |
|---|---|---|
| POST | `/edit-config` | XML body. For `<get-config>`: `<rpc-reply><data><managed-object ref function-ref>` + the object's current attributes (defaults merged with writes); missing `ref` -> `invalid-value`. For `<edit-config>`: `<ok/>` on success; `<rpc-error>` with `malformed-message` (unparseable XML), `invalid-value` (no `ref`, or empty payload for a non-delete operation) or `operation-failed` (`RPC_ERROR` fault); HTTP 504 for a `TIMEOUT` fault. `delete` / `remove` clear the object's state |
| GET | `/capabilities` | Vendor declaration (see 2.6) |

**RESTCONF surface (RFC 8040, used by RAN NF OAM for a RESTCONF ME)**

| Method | Path | Purpose / responses |
|---|---|---|
| GET | `/.well-known/host-meta` | XRD naming the RESTCONF root (`/restconf`) |
| GET | `/restconf/data/managed-element={ref}[/managed-function={function-ref}]` | The object's list entry with its current attributes (defaults merged with writes); 200 `application/yang-data+json` |
| PATCH | same | Merge the body's attributes; 204. Empty merge -> 400 `invalid-value` |
| PUT | same | Replace the object's attributes; 201 if the object was not yet written, else 204 |
| DELETE | same | 204 and clears the object's state; 404 `data-missing` if never written |
| POST | `/restconf/data`, `/restconf/data/managed-element={ref}` | Create a managed element, or a managed function under its element; 201, 409 `data-exists` if already there |

A body that is not the single list entry of the target, or whose key leaf differs from the target's, or a path other than these resources, is refused (`malformed-message` 400, `invalid-value` 400). Faults injected with `POST /faults` apply to RESTCONF writes too (`TIMEOUT` -> 504, `RPC_ERROR` -> 500 `operation-failed`, `IGNORE_WRITE` -> success status, not applied). `/edit-config/{ref}` and `/objects/{ref}` introspection see RESTCONF writes, and each protocol reads back the other's writes.

**Test-only**

| Method | Path | Purpose |
|---|---|---|
| GET | `/edit-config/{managed_object_ref}` | `{managedObjectRef, attributeChanges}`: the last applied change (null if none) |
| GET | `/objects/{managed_object_ref}?function_ref=` | `{managedObjectRef, functionRef, attributes}`: running config with defaults |
| POST | `/faults` | Body `{mode, count=1, managedObjectRef?}`; `mode` is `TIMEOUT` (HTTP 504), `RPC_ERROR` (`<rpc-error>`) or `IGNORE_WRITE` (`<ok/>`, not applied); `managedObjectRef` is `<ref>` or `<ref>/<function-ref>`, omitted = any. 201 `{faults}`; unknown mode -> 422 (plain text) |
| DELETE | `/state` | 204; forgets applied changes, object state and pending faults |

### 2.5 Interactions

Inbound HTTP only. No outbound calls, callbacks or background tasks.

### 2.6 Configuration

| Variable | Default | Meaning |
|---|---|---|
| `MOCK_O1_VENDOR_NAME` | `mock-vendor` | `vendorName` in `GET /capabilities` |
| `MOCK_O1_SUPPORTED_SERVICES` | `PROV,FM,PM,FILE,STREAM,SWM,SUBSCRIPTION,HEARTBEAT` | Comma-separated `supportedServices` |
| `MOCK_O1_VENDOR_MODES` | `O1_NETCONF,O1_RESTCONF` | Comma-separated `supportedVendorModes` |

The variables are read on every request. The container listens on :8000.

### 2.7 Error codes

No ProblemDetails. RESTCONF failures are `ietf-restconf:errors` bodies (`application/yang-data+json`, `error-type application`) with the RFC 8040 status: `invalid-value` / `malformed-message` 400, `data-missing` 404, `operation-not-supported` 405 (declared, not currently emitted), `data-exists` 409, `operation-failed` 500 (`RPC_ERROR` fault), and plain 504 for `TIMEOUT`; RAN NF OAM maps these to `RESTCONF_REQUEST_FAILED` (error body, not retried) and the retryable `RESTCONF_TIMEOUT`. NETCONF failures are NETCONF `<rpc-error>` replies with `error-type application` and `error-tag` `malformed-message`, `invalid-value` or `operation-failed`, or HTTP 504 for the `TIMEOUT` fault, which RAN NF OAM maps to `NETCONF_RPC_FAILED` (rpc-error) and the retryable `NETCONF_TIMEOUT` respectively.

### 2.8 Limits and open items

- **Not NETCONF / RESTCONF.** No SSH / TLS, no YANG, no capability negotiation, no attribute validation, no `create` / `replace` semantics distinct from `merge` (only `delete` / `remove` are special-cased). The RESTCONF side does distinguish `create` (POST, 409 if present), `replace` (PUT) and `merge` (PATCH).
- **`_applied_changes` is per `ref`**, not per function: a later write to another function of the same ME overwrites the introspection value (the running state in `_object_state` is per function).
- **No heartbeat or alarm emission.** The mock never calls RAN NF OAM; endpoint heartbeats and alarms must be sent by the test or demo.
- **Volatile.** All state is process memory.
- **Open items:** none.

## 3. Unit tests

### 3.1 Running them

```bash
cd smo/mock-o1-adaptor && PYTHONPATH=.:../shared python -m pytest tests/ -q
```

### 3.2 What is covered

| Test file | Covers | Count |
|---|---|---|
| `tests/test_main.py` | `edit-config` ok / applied-change recording / empty payload rejected / delete and remove with empty payload accepted / malformed XML / entity-expansion XML rejected; introspection of an unknown ref; configurable capability declaration; per-function state read back via `get-config`; fault consumption order; neighbour-relation, DMRO, coverage and frequency-relation defaults and writes; RESTCONF: root discovery, GET answering IOC defaults, PATCH merge read back by both protocols, PUT replace (201 / 204), POST create once (409 `data-exists`), DELETE of a missing object (`data-missing`), mismatched or malformed bodies and unmodelled paths refused, shared fault injection | 25 |

An autouse fixture resets state with `DELETE /state`.

### 3.3 What is not covered here

- The RAN NF OAM <-> mock round trip (dispatch, retry on `TIMEOUT`, `RPC_ERROR` without retry, the same over RESTCONF, `IGNORE_WRITE` detected by read-back, vendor onboarding discovery, rApp closed loops): `tests/` of `ran-nf-oam` use their own stubs; the real round trip is in `tests_integration/`.
- Real NETCONF / RESTCONF behaviour: not implemented.

## 4. References

- Call flows: [03 config write with schema check](../docs/call-flows/03-config-write-with-schema-check.md), [21 O1 vendor onboarding](../docs/call-flows/21-o1-vendor-onboarding.md)
- OpenAPI: [`../docs/openapi/mock-o1-adaptor.json`](../docs/openapi/mock-o1-adaptor.json)
- Architecture: [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) (the southbound mocks are not R1-facing); open work: [`../OPEN_ITEMS.md`](../OPEN_ITEMS.md)
- Related READMEs: [RAN NF OAM](../ran-nf-oam/README.md)
