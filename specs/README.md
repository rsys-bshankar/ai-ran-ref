# Specs

Ground-truth industry specifications this repo carries as reference
material, kept as plain files (not git submodules — `.gitmodules` at the
repo root is empty) parallel to `smo/`, the Phase 1 reference
implementation these specs describe. Moved here from the repo root so the
top level reads as `specs/` (what the industry defines) + `smo/` (what
this build implements), instead of seven unrelated top-level folders.

None of `smo/`'s code generates from these files or references their
paths — they are read-only reference material for grounding design and
implementation decisions, and for auditing `smo/`'s completeness against
the real, formal contracts (as opposed to `smo/`'s own audits so far in
`smo/OPEN_ITEMS.md` section 5, which compare against the O-RAN-SC
*source code* repos under the Repo Blueprint's ADOPT/REFERENCE list, a
different and complementary ground truth).

## What's here

- **`5G_APIs/`** — the full 3GPP 5G Core OpenAPI spec set (Release 20,
  ~540 files, most SBI-facing NFs like AMF/SMF/PCF/UDM that are out of
  SMO's scope entirely). The subset actually relevant to `smo/`'s
  modules is the ~95 `TS28xxx` management-plane specs — notably
  `TS28319_MsacNrm.yaml` (RAN NF OAM's `msacRole` gate),
  `TS28312_IntentNrm.yaml` (Policy Mgmt's Intent-driven flow),
  `TS28532_ProvMnS.yaml`/`PerfMnS.yaml`/`FileDataReportingMnS.yaml`/
  `HeartbeatNtf.yaml`/`StreamingDataMnS.yaml` (RAN NF OAM's CM write and
  PM subscription surface), `TS28111_FaultNrm.yaml`/`FaultNotifications.yaml`
  (RAN NF OAM's alarm handling), `TS28105_AiMlNrm.yaml` (AI/ML
  Workflow's own NRM — a full containment-tree model, audited in
  `smo/SPEC_AUDIT.md` and confirmed structurally different from this
  build's `aiml-fw`-shaped implementation), `TS28104_MdaNrm.yaml`/
  `TS28104_MdaReport.yaml` (RAN Analytics's own Management Data
  Analytics NRM, same audited-and-confirmed-different relationship to
  `aiml-fw-apm`'s shape), `TS29482_MLR_MLModelManagement.yaml`/
  `TS29482_MLR_ModelInformationDiscovery.yaml` (3GPP TS 29.482 AIMLE —
  a genuine Stage-3 ML-repository/model-discovery API, Release 19/20,
  much more directly applicable to MLMR's actual concept than
  TS28.105's abstract NRM containment tree; audited in
  `smo/SPEC_AUDIT.md`'s own MLMR section), and `TS28541_NrNrm.yaml`/
  `5GcNrm.yaml` (network resource modeling generally).
- **`O-RAN-WG10-O1NRM-YANGs/`**, **`O-RAN-WG10-IMDM-YANGs/`** — O-RAN's
  own O1 Network Resource Model and Information/Data Model YANG modules.
  Relevant to RAN NF OAM's `ManagedEntity`/CM write shape and
  `mock-o1-adaptor`'s NETCONF surface.
- **`O-RAN-WG4-MP-YANGs/`** — O-RU management-plane YANGs.
  **Confirmed out of scope**, per explicit direction: this build's
  degenerate single-node Phase 1 topology doesn't model
  radio-unit-specific attribute/config detail at that granularity, and
  won't. The one already-relevant WG4 piece, Software Management's base
  RPC set (`o-ran-software-management.yang`), is separately already
  implemented (`smo/SPEC_AUDIT.md`'s DME/O1 Adaptor section item 3); any
  further WG4 "logic" beyond that base RPC engine is confirmed not
  needed either.
- **`O-RAN-WG5-O-CU-MP-YANGs/`**, **`O-RAN-WG5-O-DU-MP-YANGs/`** —
  O-DU/O-CU/O-RU-Aggregator management-plane YANGs. Not a flat gap:
  per confirmed architecture direction, DME/RAN NF OAM's MnS services
  and O1 IOC data model conformance for these NF types follow a
  per-vendor own/spec/combined approach (a vendor's own model, the
  formal spec's model, or a hybrid) rather than one hard-coded shape —
  the same per-vendor capability-registry principle
  `smo/docs/ownership/DME_OWNERSHIP.md` already names as this build's
  next natural step once a second RAN vendor exists to make it real.
- **`O1_Adaptor/O1_Adaptor_MnS_Hierarchy_Mapping_v4.xlsx`** — a curated
  index across the above 3GPP/O-RAN files: every IOC reachable through
  ProvMnS's single generic `/{className}={id}` endpoint (3GPP NRM IOCs
  plus O-RAN WG10-O1NRM/WG5-O-DU/WG5-O-CU augments), the 3 genuinely
  dedicated 3GPP APIs (PM Job Control, File Data Reporting, Streaming
  Data Reporting), O-RAN WG4 Software Management's RPC/notification set,
  and (informational only) WG5's O-RU schema-mount points. User-supplied
  and hand-curated, not machine-generated — the ground truth for DME/RAN
  NF OAM's O1 Adaptor surface; see `smo/SPEC_AUDIT.md`'s new DME/O1
  Adaptor section for the line-by-line comparison against this build.
- **`o-cloud-im/`** — O-RAN WG6's real O2IMS information model
  (`resources/ORAN.O2ims.*.yaml`: Inventory, Common, Artifacts, Cluster,
  Infrastructure, Provisioning). Directly relevant to FOCOM's
  `/inventory`, `/resource-types`, `/resource-pools`,
  `/deployment-managers` routes — audited so far only against `pti-o2`'s
  Python implementation of this same interface, not this formal spec
  itself.

A line-by-line comparison against seven modules' most directly relevant
files has been done — RAN NF OAM, FOCOM, Policy Mgmt, SME, AI/ML
Workflow, RAN Analytics, and DME/O1 Adaptor (via the curated mapping
workbook above); see `smo/SPEC_AUDIT.md` for the findings. Not yet
compared against: A1 Related/Onboarding+rApp Mgmt (no relevant spec file
exists here for either). The O-RAN WG4 O-RU management-plane YANGs'
attribute-level detail is confirmed out of scope, not merely unaudited
(see above); WG5's O-DU/O-CU/O-RU-Aggregator YANGs are partially indexed
by the O1 Adaptor mapping workbook but deliberately not compared
attribute-by-attribute against one fixed shape — per-vendor own/spec/
combined conformance is the confirmed design, not a gap to close by
picking one model.
