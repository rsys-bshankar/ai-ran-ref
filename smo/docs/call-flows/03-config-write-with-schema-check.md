# Call Flow: Configuration Write, Schema-Checked, Fleet-Aware

Stitches together RAN NF OAM LLD sections 1-5 — the Option A endpoint registry and the
decomposed-PATCH aggregation that makes `PARTIAL_SUCCESS` real without violating
TS 28.532's own all-or-nothing `PATCH` semantics.

```mermaid
sequenceDiagram
    actor rApp
    participant NFOAM as RAN NF OAM SMOS
    participant Registry as O1AdaptorEndpoint Registry
    participant EP1 as O1 Adaptor (ME #1)
    participant EP2 as O1 Adaptor (ME #2)

    Note over Registry: On a timer, per ME's O1 Adaptor registers itself<br/>into the MnS Registry NRM (Option A, LLD section 1)
    Registry->>Registry: discover N endpoints, track health_status

    rApp->>NFOAM: WriteConfigurationChanges(scope, changes: [ME#1 change, ME#2 change])
    NFOAM->>NFOAM: MSAC gate check (entire-RAN scope requires admin role)
    NFOAM->>NFOAM: schema check — cm_schema_cache hit, or fetch via clause 8.3
    NFOAM->>NFOAM: schemaValidatedAt = now(), job.status: PENDING -> PROCESSING

    NFOAM->>NFOAM: decompose into sub_changes, one per ME
    NFOAM->>Registry: resolve ME#1's endpoint
    Registry-->>NFOAM: endpoint healthy
    NFOAM->>EP1: PATCH .../{className}={id} (ME#1's attribute changes)
    EP1-->>NFOAM: 200 OK
    NFOAM->>NFOAM: sub_change[ME#1].status = APPLIED

    NFOAM->>Registry: resolve ME#2's endpoint
    Registry-->>NFOAM: endpoint UNREACHABLE
    NFOAM->>NFOAM: sub_change[ME#2].status = REJECTED (ENDPOINT_UNREACHABLE)

    NFOAM->>NFOAM: aggregate: one APPLIED + one REJECTED -> PARTIAL_SUCCESS
    NFOAM-->>rApp: jobId, status=PARTIAL_SUCCESS, subChanges=[...]

    Note over NFOAM: Each individual PATCH stayed atomic — TS 28.532's own semantics<br/>were never violated. PARTIAL_SUCCESS is purely a framework-level<br/>aggregation over N independently-atomic calls (RAN NF OAM LLD section 3.2).
```

**Key decisions this flow depends on:**
- Under Option A, RAN NF OAM is a fleet aggregator over *N* per-ME O1 Adaptor instances, discovered via MnS Registry NRM — not a single-endpoint client, which was v1.3's Phase 1 assumption before this LLD pass.
- `PARTIAL_SUCCESS` exists in the schema but has no wire-level counterpart in TS 28.532 — it's realized entirely by decomposing one `WriteConfigurationChanges` call into independently-atomic per-ME `PATCH` calls and aggregating the outcomes.
- Alarm IDs raised anywhere in this flow would be minted fresh (UUID) at ingestion, never trusting a raising ME's native ID directly — closing R1UCR's own flagged, unresolved fleet-wide collision risk.
