# Call Flow: DME Real Data Movement + Lifecycle-Stage Eligibility

DME's data plane for the pull case: producers ingest real payloads as `DataRecord` rows on
a `DataJob`, and consumers page through them. Each `DMEType` declares its
`source_domain` (`LIVE_RAN` or `DIGITAL_TWIN`) and `source_context`, and each `DataJob` its
`lifecycle_stage`, so the multi-vendor/multi-Digital-Twin principle is enforced at
`CreateDataJob` time rather than left to caller discipline. See "DME source provenance and
eligibility" in `docs/ARCHITECTURE.md`.

```mermaid
sequenceDiagram
    actor Producer
    participant DME as DME
    actor Consumer as Consumer rApp / MDAF

    rect rgb(240, 255, 240)
    Note over Producer,DME: Real payload movement — training data pulled from a live RAN source
    Producer->>DME: RegisterDMEType(..., sourceDomain=LIVE_RAN, sourceContext={vendor: "VendorA", cell: "Cell-17"})
    DME-->>Producer: dmeTypeId
    Consumer->>DME: POST /data-jobs (dmeTypeId, lifecycleStage=TRAINING, ...)
    DME->>DME: _validate_lifecycle_eligibility: sourceDomain=LIVE_RAN — no restriction, passes
    DME-->>Consumer: dataJobId, status=ACTIVE
    Producer->>DME: POST /data-jobs/{id}/records (payload)
    DME-->>Producer: recordId
    Note over Producer,DME: repeats per collection interval — each ingest is its own row,<br/>ordered by producedAt, not a single mutable "latest value"
    Consumer->>DME: GET /data-jobs/{id}/records
    DME-->>Consumer: [DataRecord, ...] — paginated, oldest-first-by-cursor
    end

    rect rgb(255, 240, 240)
    Note over Producer,DME: Digital Twin — eligible for Training/Emulation, never Inference
    Producer->>DME: RegisterDMEType(..., sourceDomain=DIGITAL_TWIN, sourceContext={vendor: "SimVendor", instance: "dt-07"})
    DME-->>Producer: dmeTypeId (dtType)

    Consumer->>DME: POST /data-jobs (dmeTypeId=dtType, lifecycleStage=EMULATION, ...)
    DME->>DME: _validate_lifecycle_eligibility: DIGITAL_TWIN + EMULATION — not the<br/>one forbidden combination, passes
    DME-->>Consumer: dataJobId, status=ACTIVE

    Consumer->>DME: POST /data-jobs (dmeTypeId=dtType, lifecycleStage=INFERENCE, ...)
    DME->>DME: _validate_lifecycle_eligibility: DIGITAL_TWIN + INFERENCE — the one<br/>rule Phase-1 actually needs, enforced rather than merely documented
    DME-->>Consumer: 422 DIGITAL_TWIN_INFERENCE_NOT_ELIGIBLE
    Note over Consumer,DME: a real inference decision must be grounded in a live RAN<br/>source — a Digital Twin's synthetic data never substitutes for it
    end
```

**Key decisions this flow depends on:**
- `DataRecord` rows are append-only per `DataJob`, ordered by `producedAt` — there is no single mutable "current value" a producer overwrites; a consumer pulling twice sees every record produced since its last read (subject to pagination), not just the latest.
- The eligibility check reads `DMEType.source_domain` (set at registration) and the `DataJob`'s requested `lifecycleStage` — both are optional; a type or job that declares neither skips the check, the same permissive default other optional cross-references use (`_validate_job_definition_schema` skips an unknown type; `_validate_delivery_method` skips the offer check for a type with no `DataOffer`).
- Only one combination is forbidden — `DIGITAL_TWIN` + `INFERENCE` (`DIGITAL_TWIN_INFERENCE_NOT_ELIGIBLE`, 422) — not "Digital Twin data is second-class everywhere." `TRAINING`, `TESTING`, `EMULATION` and `CLOSED_LOOP_FEEDBACK` are all legitimate for a Digital Twin source; only a live inference decision must trace back to a real RAN source.
- `sourceContext` is a flexible JSON dict (vendor/product/release/instance/node/cell — whichever a producer populates), not eight forced columns, since nothing queries most of them individually. The vendor capability registry (call flow 21) lives in RAN NF OAM and is keyed by the managed element's vendor, not by this field.
- Job push (`_push_job_to_producers`, call flow 11) and record ingestion are separate mechanisms — registering a `DataJob` notifies every supporting producer, but records arrive only when a producer posts them to `POST /data-jobs/{id}/records`. RAN NF OAM does so for PM counters whenever an NF delivers a PM report (`POST /pm-reports`, used by the reference rApps in call flows 22–25); other producers' collection loops are outside this build (Phase 1: elided).
