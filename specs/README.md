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
  (RAN NF OAM's alarm handling), and `TS28541_NrNrm.yaml`/`5GcNrm.yaml`
  (network resource modeling generally).
- **`O-RAN-WG10-O1NRM-YANGs/`**, **`O-RAN-WG10-IMDM-YANGs/`** — O-RAN's
  own O1 Network Resource Model and Information/Data Model YANG modules.
  Relevant to RAN NF OAM's `ManagedEntity`/CM write shape and
  `mock-o1-adaptor`'s NETCONF surface.
- **`O-RAN-WG4-MP-YANGs/`**, **`O-RAN-WG5-O-CU-MP-YANGs/`**,
  **`O-RAN-WG5-O-DU-MP-YANGs/`** — O-RU/O-CU/O-DU management-plane YANG
  models (radio-unit-specific attribute/config detail this build's
  degenerate single-node Phase 1 topology doesn't model at that
  granularity — likely stays out of scope, but not yet audited against).
- **`o-cloud-im/`** — O-RAN WG6's real O2IMS information model
  (`resources/ORAN.O2ims.*.yaml`: Inventory, Common, Artifacts, Cluster,
  Infrastructure, Provisioning). Directly relevant to FOCOM's
  `/inventory`, `/resource-types`, `/resource-pools`,
  `/deployment-managers` routes — audited so far only against `pti-o2`'s
  Python implementation of this same interface, not this formal spec
  itself.

No line-by-line comparison against these files has been done yet as of
this note; `smo/OPEN_ITEMS.md` tracks that as upcoming work.
