# O1 Termination — Per-Vendor Onboarding Guide

**Status: implemented in Wave 9** (`docs/roadmap/WAVES_4_TO_10_WORK_ITEMS.md`
W9-01..06; `ran-nf-oam/app/vendors.py`, call flow 21). The registry is
built close to the sketch below. Differences:

- **The capability lives in a per-vendor registry.** `vendor_capability`
  holds the vendor's services, conformance mode and vendor modes.
  `O1AdaptorEndpoint.supported_services` can narrow those services for one
  endpoint, but never widen them.
- **Descriptors are generated from NRM OpenAPI definitions.**
  `scripts/ingest_cm_schema.py` reads definitions such as
  `specs/5G_APIs/TS28541_NrNrm.yaml`, not YANG. The TS 28.541 descriptor is
  bundled as the default `SPEC` model, and a YANG front end is still future
  work.
- **Discovery reads only an adaptor that is already registered.**
  `POST /vendor-onboarding` names a registered managed element
  (`discoverFrom`). It reads that adaptor's `/capabilities`, never a URL
  supplied in the request.
- **DME passes refusals back unchanged.** It forwards RAN NF OAM's 4xx
  rather than duplicating the checks.
- **It is built before a second real vendor exists.** The Wave 9 work
  items call for it now; the mock O1 adaptor's configurable
  `GET /capabilities` stands in for a second vendor's declaration.

The rest of this document is the original sketch, kept as the design
rationale. Its "already works" / "missing" sections describe the code as
it was before Wave 9.

**Original status: design sketch, not implemented.** No code in this build enforces
any of the capability-registry mechanics below — every "already works"
claim here is checked against the real current code (file/line cited);
every "sketch" claim is explicitly marked as unbuilt. Written per explicit
direction, to answer one question precisely: when a second real RAN
vendor or Digital Twin shows up, does onboarding it mean writing new
application code per vendor, or is there a better shape? This sketches
that better shape, so it's ready to build the day a second vendor makes
it concrete — not before, per `docs/ownership/DME_OWNERSHIP.md`'s own
standing note that the registry isn't built "since no second RAN vendor
exists in this build to make [it] real rather than speculative."

## The three things "per vendor" actually means

Every RAN vendor's (or Digital Twin's) O1 termination is really three
independent concerns, and conflating any two of them is what makes
vendor onboarding feel like it requires new code:

1. **MnS transport — the wire protocol and encoding.** NETCONF+XML,
   full stop, for this build today (`ManagedEntity.o1_protocol`;
   RESTCONF is explicitly rejected with `PROTOCOL_NOT_SUPPORTED` rather
   than silently applied — `ran-nf-oam/app/main.py:155-162`). This axis
   isn't yet a per-vendor variable in this build at all: there's exactly
   one transport implementation, and a vendor either speaks it or can't
   be onboarded today.
2. **MnS services — which O1 operation categories a vendor's adaptor
   actually implements.** Provisioning/CM (`edit-config`
   create/replace/merge/delete), Fault Management, Performance
   Management, File Management, Streaming Data, Software Management,
   Subscription control, Heartbeat. A real vendor need not implement all
   eight — one O-RU might expose Fault Management and Heartbeat only;
   another, the full set. This is a *presence* question (does the
   vendor support this service at all), independent of transport.
3. **MnS IOC data model — the information/data model conformance**
   *within* whichever services a vendor does support. What the actual
   attribute names, class names, and value shapes are: WG4 for O-RU, WG5
   for O-DU/O-CU/O-RU-Aggregator, WG10-O1NRM/IMDM for the cross-cutting
   IOC tree (see `specs/README.md`) — or a vendor's own proprietary
   shape, or a hybrid ("own/spec/combined", `SPEC_AUDIT.md`'s DME/O1
   Adaptor section item 4). This is a *shape* question, not a presence
   one: given a vendor does support Fault Management, whose attribute
   names does its `Alarm` payload use?

A vendor can differ on all three axes independently. This guide sketches
a registry for axes 2 and 3 (services and data model), because axis 1
(transport) already has a real, working, vendor-generic implementation —
see below — while axes 2 and 3 currently have none.

## What already works today, with zero new code

Checked directly against the running code, not assumed. This is
entirely axis 1 (transport) — the one axis that's already
vendor-generic:

- **Registering a new vendor's endpoint is already a real, generic
  API**, not something requiring a new route per vendor:
  `POST /o1-adaptor-endpoints` (`ran-nf-oam/app/main.py:72`) takes
  `vendorName`, `entityType`, `o1Protocol`, `protocolSupport`, and
  `adaptorUri` as plain request fields, and creates both the
  `O1AdaptorEndpoint` and `ManagedEntity` rows. `ManagedEntity.vendor_name`
  (`models.py:28`) is already a first-class column today — a second
  vendor's ME registers through the exact same call a first vendor's did.
- **The NETCONF write-dispatch path is already vendor-generic.**
  `netconf_client.py`'s `build_edit_config_rpc`/`send_edit_config` build
  and POST a real RFC 6241 `<edit-config>` RPC to whatever `adaptor_uri`
  that ME registered — nothing in this path is hardcoded to one vendor's
  shape. `attribute_changes` (`WriteConfigSubChange.attribute_changes`,
  a free-form JSON dict) carries whatever attribute names the caller
  sends, so a second vendor whose IOC attribute *names* differ already
  dispatches mechanically — at the cost of zero validation (see "what's
  missing" below).
- **DME's provenance model is already vendor-shaped, not
  hard-coded.** `DMEType.source_context` (`dme/app/models.py:56`) is a
  flexible JSON dict — vendor/product/release/instance/node/cell,
  whichever a producer actually populates — specifically so a second
  vendor or a Digital Twin doesn't need new columns, per
  `DME_OWNERSHIP.md`'s "Source provenance" section.
- **`mock-o1-adaptor` already demonstrates the pattern's ceiling
  today**: it's a single generic `POST /edit-config` responder
  (`mock-o1-adaptor/app/main.py:33`) with no vendor-specific logic at
  all — it just validates the RPC shape and replies `<ok/>`. A real
  second vendor's O1 Adaptor is a drop-in replacement for this exact
  contract, nothing in `ran-nf-oam` would need to change to talk to it.

So the transport axis is already solved for anything that speaks
RFC 6241-shaped `edit-config` over HTTP. RESTCONF or a genuinely
proprietary vendor API is the one transport-level gap — see "what this
doesn't solve" below.

## What's genuinely missing — axes 2 and 3

Also checked directly, not assumed:

**Axis 2 (services) has no capability gating anywhere.**
`O1AdaptorEndpoint.protocol_support` (`models.py:16`) is a list, but
grepping every real call site shows it only ever holds transport values
— `["NETCONF"]`, never a service name (`test_main.py:229,254,265` and
every fixture in `test_main.py:386-387`). No route checks it as a
service-presence gate:

- `ingest_alarm` (`main.py:202`) creates an `Alarm` row straight from
  `managed_element_ref` — it never even looks up that ME's
  `O1AdaptorEndpoint`, let alone whether it declares Fault Management
  support.
- `subscribe_pm` (`main.py:261`) and `software_update` (`main.py:319`)
  are the same shape: `managed_element_ref` in, a row created, no
  capability check anywhere in between.

So today, a vendor whose real O-RU has no Software Management support
at all would still accept a `POST /software-management-jobs` against it
with no rejection — this build has no way to say "this vendor doesn't
do that" before the fact, for any of the eight service categories.

**Axis 3 (data model) has no schema tracking at all.**
`CMSchemaCache` exists but is completely unused. (`ran-nf-oam/
app/models.py:73`: `schema_name`/`revision`/`location`/`type`/
`cached_at`.) Grepping the entire module for
`CMSchemaCache(`/`cm_schema_cache`/`schema_name` finds it only in the
model definition and one unrelated import — no route ever creates,
reads, or checks against a row in this table.
`WriteConfigJob.schema_validated_at` (`models.py:89`) is stamped with
the current time (`main.py:124`) unconditionally — it records *that* a
write happened, not that anything was actually validated against a
schema. `OPEN_ITEMS.md` already names this precisely: "the separate
`cm_schema_cache` fetch path is untouched."

**Neither axis has a conformance-mode flag anywhere.** "Own / spec /
combined" (`SPEC_AUDIT.md` item 4, `DME_OWNERSHIP.md`'s multi-vendor
principle) is a stated design intent, not a field that exists on
`ManagedEntity` or `O1AdaptorEndpoint` today — and, per the axis
distinction above, it's really only meaningful for axis 3 (data-model
*shape*); axis 2 is a plain presence/absence list, not something a
vendor can be "own" or "spec" about — either it implements Fault
Management or it doesn't.

## The sketch

Two small, independent registries — deliberately *data*-driven, so
onboarding a vendor means feeding it descriptors, not writing Python.
Kept separate on purpose, matching the axis distinction above: axis 2 is
a presence check, axis 3 is a shape check, and conflating them into one
mechanism would smuggle back the same confusion this guide started with.

**Axis 2 — service capability list, on `O1AdaptorEndpoint` itself:**

1. `protocol_support` already exists as a `list[str]` column
   (`models.py:16`) — today it's only ever populated with transport
   values. Add a sibling `supported_services: list[str]` column, values
   drawn from a fixed enum: `PROV`/`FM`/`PM`/`FILE`/`STREAM`/`SWM`/
   `SUBSCRIPTION`/`HEARTBEAT`. Set once, at `register_o1_adaptor_endpoint`
   time, from whatever the vendor's own O1 Adaptor declares it supports
   — not derived from YANG, since this is a presence list, not a schema.
2. One small, generic guard reused by every service route, not written
   per vendor: `_require_service(db, managed_element_ref, "FM")` — the
   same shape as the existing `_validate_lifecycle_eligibility` in DME's
   `main.py` — called at the top of `ingest_alarm`, `subscribe_pm`, and
   `software_update` (today, none of the three call anything like it).
   An ME with no endpoint registered at all skips the check, same
   permissive default this build already uses everywhere a
   cross-reference is optional.

**Axis 3 — IOC data-model descriptor, finally giving `CMSchemaCache` a
real writer and reader:**

3. Extend `O1AdaptorEndpoint` with `conformance_mode` (`OWN`/`SPEC`/
   `COMBINED`) and `schema_ref`, a foreign key into `CMSchemaCache`.
   Nothing else needs a new column — the descriptor's actual *content*
   (which IOC classes/attributes this vendor's data conforms to) lives in
   the schema blob `schema_ref` points at, not as new SQL columns per
   vendor.
4. **YANG ingestion is mechanical, not hand-transcription.** A one-time
   step per vendor (a script, not a new endpoint): run the vendor's real
   YANG modules — or the O-RAN WG4/WG5/WG10 modules already sitting in
   `specs/` if `conformance_mode=SPEC` — through a real YANG toolchain
   (`pyang`/`pyangbind`) to emit a JSON capability descriptor: IOC class
   names, attribute names + types. That JSON is what
   `CMSchemaCache.location` points at (the column already exists for
   exactly this). No one hand-writes a mapping table per vendor; the
   toolchain derives it from the vendor's own YANG source of truth.
5. **A schema check reused by every write path, not written per vendor.**
   Before `send_edit_config` dispatches, look up the target ME's
   `schema_ref` and check the requested `attribute_changes` keys exist
   in that descriptor: one small, generic function
   (`_validate_against_schema(db, managed_element_ref,
   attribute_changes)`), not per-vendor branching. An ME with no
   `schema_ref` registered skips the check, same permissive default as
   the axis-2 guard above.
6. **`conformance_mode` decides *which* descriptor(s) the check runs
   against**, making "own/spec/combined" concrete instead of aspirational:
   - `SPEC`: validate strictly against the ingested O-RAN/3GPP YANG
     descriptor only.
   - `OWN`: validate against the vendor's own ingested YANG only — no
     cross-check against the formal spec at all.
   - `COMBINED`: validate against the spec descriptor, but also accept
     any attribute the vendor's own descriptor separately declares (a
     documented, per-vendor augment list) — spec compliance plus
     named extensions, not an unbounded escape hatch.

**Both axes reach DME's `/actions` mediation the same way.**
`DmeActionRecord` already carries the target `managed_element_ref`
(`dme/app/models.py:167`, forwarded via `_r1.post("/ran-nf-oam/
config-jobs", ...)` at `dme/app/main.py:745`) — the axis-2 presence
guard and the axis-3 schema check both run there too, so an rApp's
decision against a service a vendor doesn't implement, or an
IOC/attribute its data model doesn't support, gets a real 4xx from DME
directly, instead of round-tripping to `ran-nf-oam` and back.

## What onboarding a second vendor looks like once this is built

1. Find out which of the eight service categories the vendor's real O1
   Adaptor implements (a spec-sheet question, not a YANG-parsing one).
2. Get the vendor's real YANG bundle for whichever services it does
   support (or point at the O-RAN spec directly if it's `SPEC`-only).
3. Run the ingestion script once → a JSON capability descriptor, stored
   wherever `CMSchemaCache.location` points (e.g. object storage, or a
   path this build already reads from).
4. `POST /o1-adaptor-endpoints` with `vendorName`, `supportedServices`
   (from step 1), the new `conformanceMode`, and a reference to the
   descriptor from step 3 — the same call that already works today, with
   three new fields.
5. Nothing else changes in `ran-nf-oam` or `dme` code. The axis-2 guard
   and the axis-3 schema check both read whichever row that ME's
   registration created, at request time.

Three vendors means three service lists, three descriptors, and three
registration calls — not three code forks.

## What this sketch does *not* solve

Being honest about its ceiling, matching this build's established
"confirmed elision, not an oversight" discipline:

- **Wire-protocol differences.** A vendor whose southbound isn't
  RFC 6241-shaped `edit-config` over HTTP — real NETCONF/SSH via
  `ncclient`, RESTCONF, or a genuinely proprietary API — needs an actual
  new transport adapter, the same "RESTCONF/vendor-API adapters... not
  built this wave" extension point `DME_OWNERSHIP.md` already names.
  This sketch only removes the need for new code on the *services* and
  *data-model* axes; a new *transport* still needs a new client module
  (one per transport family, not one per vendor —
  `ManagedEntity.o1_protocol` already exists to select it).
- **Semantic differences the YANG itself can't express** — e.g. a
  vendor's `edit-config` accepting an attribute but silently ignoring it
  under some condition the schema doesn't declare. A capability
  descriptor derived from YANG documents *shape*, not runtime behavior;
  this class of gap is only found by real integration testing against
  that vendor, same as any other formal-spec-vs-implementation gap this
  build's own `SPEC_AUDIT.md` already catalogs elsewhere.
- **Building it now.** Per the original discussion: this stays a design
  sketch until a second real RAN vendor or Digital Twin exists in this
  build to build and validate it against — building it speculatively
  against zero real second vendors would be exactly the kind of
  fabricated-for-completeness work this build's own discipline (see
  `SPEC_AUDIT.md`'s "honest partial mapping, not fabricated" precedent)
  avoids elsewhere.

## References

- `docs/ownership/DME_OWNERSHIP.md` — the standing multi-vendor
  principle this sketch makes concrete.
- `SPEC_AUDIT.md`'s DME/O1 Adaptor section, items 1, 3, 4 — the
  addressing/RPC/IOC findings this sketch builds on.
- `specs/README.md` — the WG4/WG5/WG10 YANG catalog this sketch's
  ingestion step would read from in `SPEC`/`COMBINED` mode.
- `OPEN_ITEMS.md`'s "CM cache sync method" entry — where
  `cm_schema_cache`'s current unused state was first named.
- `ran-nf-oam/app/netconf_client.py`, `ran-nf-oam/app/main.py`'s
  `register_o1_adaptor_endpoint` — the real, already-generic code this
  sketch extends rather than replaces.
