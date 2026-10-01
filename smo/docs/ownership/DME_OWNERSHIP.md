# DME Ownership

Status: **revised, partially implemented** — Wave 3 (`dme/app/models.py`,
`dme/app/main.py`). See `docs/architecture/SERVICE_OWNERSHIP_MATRIX.md`
for how this fits alongside AIMgF/MLMR/MLLF/MDAF/Intent Service, and
`docs/architecture/AI_PLATFORM_BASELINE.md` for the layered picture.

This doc replaces DME's previous implicit scope (a pure R1AP/ICS-style
data-job/offer broker, Wave 0-2) with the revised architecture agreed
for Wave 3: DME is both an **AI/ML data-plane abstraction** and an **O1
actuation-mediation layer**, serving both MDAF and rApps directly, and
must preserve source/vendor/domain provenance so a multi-vendor,
multi-Digital-Twin deployment never silently mixes data across sources.
The formal-spec grounding for the O1-facing half is
`specs/O1_Adaptor/O1_Adaptor_MnS_Hierarchy_Mapping_v4.xlsx`, audited in
`SPEC_AUDIT.md`'s "DME / O1 Adaptor" section.

## Mission

DME is the vendor-neutral canonical data-and-control broker between the
Non-RT RIC/rApp environment and heterogeneous RAN/Digital Twin sources.
It has two responsibilities, not one:

1. **Data-plane** — acquire, tag with provenance, store and expose the
   datasets the AI/ML lifecycle needs (training/testing/emulation/
   inference/closed-loop feedback), for both rApps and MDAF.
2. **Control/actuation mediation** — translate an rApp's inference
   decision into an O1 action, forwarded to the service that actually
   speaks the wire protocol.

DME is not itself a NETCONF/RESTCONF client and does not become one this
wave. `ran-nf-oam` already owns real O1 protocol dispatch
(`netconf_client.py`'s `edit-config` RPCs against its own
`ManagedEntity`/`O1AdaptorEndpoint` registry) — DME's action-mediation
route is a thin, provenance-recording layer in front of that, forwarding
to `ran-nf-oam`'s existing `POST /config-jobs`. NETCONF today, RESTCONF/
vendor-API later, stay *beneath* DME's contract, never inside it — a
consumer's request shape doesn't change when a vendor's transport does.

## Phase-1 scope (explicitly confirmed, not an oversight)

- **No A1/xApp/Near-RT RIC integration.** Model inference runs inside
  the rApp itself, against live RAN data DME brokers — not against an
  xApp in the Near-RT RIC. The A1 interface stays architecturally
  present elsewhere in this build (`a1-related/`) but is not part of
  this loop.
- **NETCONF only**, matching `ran-nf-oam`'s existing, confirmed protocol
  choice. RESTCONF/vendor-API adapters are a named extension point, not
  built this wave (no second protocol or second RAN vendor exists in
  this build to make that real rather than speculative). *(Wave 9 added
  RAN NF OAM's per-vendor capability registry, which declares vendor
  modes, MnS services and a data-model conformance mode — call flow 21.
  DME forwards `className` and passes a refused write's 4xx straight back
  to the rApp. A RESTCONF transport is still not built.)*
- **Digital Twin participates in Training and Emulation, never
  Inference.** Enforced as real, DB-level validation (see below), not a
  convention left to callers.

## Owns

**Type/producer registry** (unchanged from Wave 0-2): `DMEType`,
`DMETypeSubscription`, `DataOffer` — production capability registration,
discovery, health-derived status, type-change subscriptions.

**Source provenance** (new, Wave 3): every `DMEType` now carries
`source_domain` (`LIVE_RAN` | `DIGITAL_TWIN`) and a `source_context`
JSON blob (vendor/product/release/instance/node/cell — whichever of
that hierarchy a given producer actually populates; deliberately a
flexible dict, not eight forced columns, since nothing in this build yet
needs to query most of them individually). Every `DataJob` now carries
`lifecycle_stage` (`TRAINING`|`TESTING`|`EMULATION`|`INFERENCE`|
`CLOSED_LOOP_FEEDBACK`). `create_data_job` rejects
`source_domain=DIGITAL_TWIN` + `lifecycle_stage=INFERENCE` — the one
eligibility rule Phase-1 actually needs, enforced rather than merely
documented.

**Real data-plane storage** (new, Wave 3): `DataRecord` — actual
payloads a producer ingests against a `DataJob`
(`POST /data-jobs/{id}/records`) and a consumer (rApp or MDAF, no
distinction at this layer) fetches
(`GET /data-jobs/{id}/records`). Previously DME only ever brokered
job/offer *metadata* and left real data movement to the negotiated
delivery method (pull/push/streaming) happening entirely outside DME;
this closes that gap for the pull case with a genuine DB-backed store,
without taking on a full data-lake redesign.

**O1 action mediation** (new, Wave 3): `DmeActionRecord` +
`POST /actions` — records an rApp's decision (target `className`/
`managed_element_ref`, requested changes, source context) and forwards
it to `ran-nf-oam`'s `POST /config-jobs`, returning that job's id/status.
DME's own record is the audit trail of *what an AI/ML decision asked
for*; `ran-nf-oam`'s `WriteConfigJob`/`WriteConfigSubChange` remain the
record of *what NETCONF actually did*.

## Does NOT own

| Concern | Owner |
|---|---|
| Real NETCONF/RESTCONF protocol dispatch | `ran-nf-oam` |
| O1 endpoint registry, ME/MF addressing, alarms, PM/CM/SWM jobs | `ran-nf-oam` |
| Analytics report/subscription *output* | MDAF |
| Model lifecycle, training/validation/emulation orchestration | AIMgF |
| Model repository, artifacts | MLMR |

## Two paths, not one

- **rApp → DME → O1 (action path)**: an rApp's inference decision goes
  through DME's `/actions` mediation route, which forwards to
  `ran-nf-oam`. Nothing else — including MDAF — is on this path.
- **rApp → MDAF, and MDAF → DME (data path only)**: MDAF is a consumer
  of DME's data plane for training/testing/emulation/inference input
  and closed-loop feedback/monitoring, the same as any rApp is. MDAF
  never reaches the O1 action path — `publish_report`'s `input_sources`
  are validated against real DME `DataJob`s (a cross-service check via
  `R1Client`, same bare-UUID-reference convention used for NFO/AIMgF
  cross-service references elsewhere in this build), so a report can no
  longer cite data that never actually came from DME.

## Multi-vendor / multi-Digital-Twin principle

Recorded here as the standing design constraint this wave's
`source_domain`/`source_context` fields exist to serve, even though no
second RAN vendor or second Digital Twin instance exists in this build
yet to exercise it concretely: every dataset, record and control
operation DME brokers must carry enough source identity that a future
multi-vendor, multi-DT deployment can never silently mix data across
producers. A full per-vendor capability registry (tracking which IOCs/
protocols/extensions each vendor's O1 Adaptor actually supports) is the
next natural step once a second vendor makes it real rather than
speculative — not built this wave. See
`docs/architecture/O1_VENDOR_ONBOARDING_GUIDE.md` for a concrete sketch
of that registry's shape (checked against the real, already-generic
`ran-nf-oam` endpoint-registration/NETCONF-dispatch code), ready to build
the day a second real vendor exists.

**O-RAN WG5 conformance is confirmed to follow this same per-vendor
principle**, not one fixed shape: for O-DU/O-CU/O-RU-Aggregator NF
types, each vendor's (RAN or Digital Twin) DME/RAN NF OAM MnS services
and O1 IOC data model will conform via its own model, the formal WG5
spec's model, or a combined/hybrid approach, chosen per-vendor rather
than hard-coded once-for-all — the same "flexible dict, not forced
columns" philosophy `source_context` already applies to source
provenance, extended here to conformance shape itself. (WG4, by
contrast — O-RU-only management-plane YANGs — is confirmed fully out of
scope for this build, aside from the Software Management RPC engine
`ran-nf-oam` already implements; see `SPEC_AUDIT.md`'s DME/O1 Adaptor
section items 3-4.)
