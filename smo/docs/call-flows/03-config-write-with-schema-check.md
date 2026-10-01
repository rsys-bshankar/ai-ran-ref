# Call Flow: Configuration Write, Fleet-Aware, Two Entry Paths

How a configuration change reaches real managed elements (RAN NF OAM LLD sections 1-5).
Under the Option A endpoint registry, RAN NF OAM is a fleet aggregator: it decomposes one
`WriteConfigurationChanges` call into one sub-change per managed element (ME), sends each
to that ME's O1 Adaptor, and aggregates the outcomes — which makes `PARTIAL_SUCCESS` real
without violating TS 28.532's all-or-nothing `PATCH` semantics. Dispatch is an RFC 6241
`<edit-config>` XML RPC POSTed to the endpoint's `adaptor_uri` (`netconf_client.py`), not a
REST verb.

There are two entry paths: an rApp calls RAN NF OAM directly (Path A), or calls DME's O1
action-mediation route `POST /dme/actions` (Path B), which records the decision's
provenance and forwards it to the same route (see "DME data path and action path" in
`docs/ARCHITECTURE.md`).

**Schema check.** Before any job is created, `POST /config-jobs` checks each change against
the vendor capability registry (`ran-nf-oam/app/vendors.py`, call flow 21):

- the ME's vendor must implement Provisioning, else 409 `O1_SERVICE_NOT_SUPPORTED`;
- its class, attributes and enum values must exist in the data model the vendor's
  conformance mode selects, else 422 `SCHEMA_VALIDATION_FAILED`, naming every offending attribute.

`cm_schema_cache` holds those data models. An ME with no vendor capability registered is
not schema-checked.

**Relation to call flows 02 and 09.** This flow is the CM-write mechanism itself,
regardless of who decided the change was needed. An rApp that has pulled a prediction via
DME (call flow 02) may act on it out of band through Path A or Path B; neither entry point
ties the call back to a specific inference job. An rApp instance whose onboarding-time
`autonomyMode` is `AUTONOMOUS`/`ASSIST` uses `RequestAutonomyDispatch` (call flow 09)
instead: the outcome is enacted as an `Intent`, and SA SMOS's generic O1-CM intent handler
then writes it through Path B with itself as the caller (call flows 09 and 22). Calling
Path A/B directly remains the manual route, and the only one for a `SHADOW` instance
(nothing is enforced) or an rApp acting on its own decision.

```mermaid
sequenceDiagram
    actor rApp
    participant DME as DME
    participant NFOAM as RAN NF OAM SMOS
    participant Registry as O1AdaptorEndpoint Registry
    participant EP1 as O1 Adaptor (ME #1)

    Note over EP1,Registry: Each ME's O1 Adaptor self-registers once, via<br/>POST /o1-adaptor-endpoints (vendorName, entityType, o1Protocol,<br/>protocolSupport, adaptorUri) — Option A, LLD section 1
    Registry->>Registry: health_status starts DISCOVERED, ages to<br/>UNREACHABLE without a timely heartbeat

    rect rgb(240, 248, 255)
    Note over rApp,NFOAM: Path A — direct rApp -> RAN NF OAM
    rApp->>NFOAM: WriteConfigurationChanges(scope, changes: [ME#1 change, ME#2 change])
    NFOAM->>NFOAM: MSAC gate check (entire-RAN scope requires admin role)
    NFOAM->>NFOAM: schema check against the vendor's data model<br/>(skipped for an ME with no vendor capability), job.schemaValidatedAt = now()
    NFOAM->>NFOAM: job.status: PENDING -> PROCESSING
    end

    rect rgb(255, 240, 240)
    Note over rApp,DME: Path B — DME-mediated (Wave 3's own new role)
    rApp->>DME: POST /actions (requestedBy, scope, changes, sourceContext)
    DME->>DME: create DmeActionRecord — DME's own audit trail of<br/>*what the rApp's decision asked for*, distinct from<br/>RAN NF OAM's own record of what NETCONF actually did
    DME->>NFOAM: POST /ran-nf-oam/config-jobs (same requestedBy/scope/changes, className stripped)
    Note over NFOAM: from here, identical to Path A — DME is a thin,<br/>provenance-recording layer in front of the same dispatch,<br/>not a second implementation of it
    end

    NFOAM->>NFOAM: decompose into sub_changes, one per ME
    NFOAM->>Registry: resolve ME#1's endpoint
    Registry-->>NFOAM: endpoint healthy, protocol=NETCONF
    NFOAM->>EP1: POST <rpc><edit-config>...<managed-object ref="ME#1" operation="merge">...(RFC 6241, netconf_client.py)
    EP1-->>NFOAM: <rpc-reply><ok/>
    NFOAM->>NFOAM: sub_change[ME#1].status = APPLIED

    NFOAM->>Registry: resolve ME#2's endpoint
    Registry-->>NFOAM: endpoint UNREACHABLE
    NFOAM->>NFOAM: sub_change[ME#2].status = REJECTED (ENDPOINT_UNREACHABLE)

    NFOAM->>NFOAM: aggregate: one APPLIED + one REJECTED -> PARTIAL_SUCCESS
    alt Path A
        NFOAM-->>rApp: jobId, status=PARTIAL_SUCCESS, subChanges=[...]
    else Path B
        NFOAM-->>DME: jobId, status=PARTIAL_SUCCESS
        DME->>DME: record.forwardedJobId = jobId, record.status = PARTIAL_SUCCESS
        DME-->>rApp: actionId, forwardedJobId, status=PARTIAL_SUCCESS
    end

    Note over NFOAM: Each individual edit-config RPC stayed atomic — TS 28.532's own semantics<br/>were never violated. PARTIAL_SUCCESS is purely a framework-level<br/>aggregation over N independently-atomic calls (RAN NF OAM LLD section 3.2).
```

**Key decisions this flow depends on:**
- Under Option A, RAN NF OAM is a fleet aggregator over *N* per-ME O1 Adaptor instances, discovered via `POST /o1-adaptor-endpoints` self-registration — not a single-endpoint client. (Real MnS Registry NRM polling is a confirmed elision — this is the same lighter self-registration substitute DME's own producer registration and SME's own provider/invoker registration both use.)
- `PARTIAL_SUCCESS` exists in the schema but has no wire-level counterpart in TS 28.532 — it's realized entirely by decomposing one `WriteConfigurationChanges` call into independently-atomic per-ME `edit-config` RPCs and aggregating the outcomes.
- **Path B never duplicates Path A's dispatch logic.** DME's `/actions` route is deliberately a thin forward — one HTTP call to the same `POST /config-jobs` Path A's caller hits directly — so a dispatch-logic change (a new rejection reason, a new protocol) never needs touching in two places. A 4xx from RAN NF OAM (capability or schema refusal) is passed back unchanged and the action is recorded `REJECTED`.
- An ME provisioned for RESTCONF (`o1_protocol != "NETCONF"`) is rejected with `PROTOCOL_NOT_SUPPORTED` on either path — there is no silent fallback to "applied," and no RESTCONF dispatch exists (OPEN_ITEMS.md OI-1-cm-sync-restconf).
- Alarm IDs raised anywhere in this flow are minted fresh (UUID) at ingestion, never trusting a raising ME's native ID directly — closing R1UCR's flagged fleet-wide collision risk.
