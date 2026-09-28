# O1 Termination — Per-Vendor Onboarding Guide

**Status: design sketch, not implemented.** No code in this build enforces
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

## The two things "per vendor" actually means

Every RAN vendor's (or Digital Twin's) O1 termination is really two
independent concerns, and conflating them is what makes vendor onboarding
feel like it requires new code:

1. **MnS services — the protocol/transport termination.** How RPCs and
   notifications are physically exchanged: NETCONF vs RESTCONF vs a
   proprietary vendor API, which operations that transport supports, and
   where the endpoint lives.
2. **O1 IOC data model — the information/data model conformance.** What
   the actual attribute names, class names, and value shapes are on the
   wire: WG4 for O-RU, WG5 for O-DU/O-CU/O-RU-Aggregator, WG10-O1NRM/IMDM
   for the cross-cutting IOC tree (see `specs/README.md`) — or a vendor's
   own proprietary shape instead, or a hybrid ("own/spec/combined",
   `SPEC_AUDIT.md`'s DME/O1 Adaptor section item 4).

A vendor can differ on either axis independently of the other. This
guide sketches a registry for axis 2 (data model), because axis 1
(transport) already has a real, working extension point — see below —
while axis 2 currently has none.

## What already works today, with zero new code

Checked directly against the running code, not assumed:

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

## What's genuinely missing — the data-model axis

Also checked directly, not assumed:

- **`CMSchemaCache` exists but is completely unused.**
  (`ran-nf-oam/app/models.py:73`: `schema_name`/`revision`/`location`/
  `type`/`cached_at`.) Grepping the entire module for
  `CMSchemaCache(`/`cm_schema_cache`/`schema_name` finds it only in the
  model definition and one unrelated import — no route ever creates,
  reads, or checks against a row in this table.
  `WriteConfigJob.schema_validated_at` (`models.py:89`) is stamped with
  the current time (`main.py:124`) unconditionally — it records *that* a
  write happened, not that anything was actually validated against a
  schema. `OPEN_ITEMS.md` already names this precisely: "the separate
  `cm_schema_cache` fetch path is untouched." This is the honest,
  pre-existing gap the sketch below closes.
- **No capability descriptor per vendor.** Nothing today records which
  IOC classes, attributes, or RPCs a given `O1AdaptorEndpoint` actually
  supports. A config write or DME `/actions` mediation request is fired
  at whatever `adaptor_uri` is registered and hoped for — there's no way
  to reject "this vendor's O-DU doesn't expose that attribute" before
  the RPC round-trip.
- **No conformance-mode flag anywhere.** "Own / spec / combined"
  (`SPEC_AUDIT.md` item 4, `DME_OWNERSHIP.md`'s multi-vendor principle)
  is a stated design intent, not a field that exists on `ManagedEntity`
  or `O1AdaptorEndpoint` today.

## The sketch

A capability registry that makes the above real, in five pieces —
deliberately *data*-driven, so onboarding a vendor means feeding it a
descriptor, not writing Python:

1. **Vendor capability descriptor.** Extend `O1AdaptorEndpoint` (or a new
   sibling table keyed the same way) with:
   - `conformance_mode`: `OWN` | `SPEC` | `COMBINED`.
   - `schema_ref`: a foreign key into `CMSchemaCache` — finally giving
     that table a real writer and reader.
   - Nothing else needs a new column: the descriptor's actual *content*
     (which IOC classes/attributes/RPCs this vendor supports) lives in
     the schema blob referenced by `schema_ref`, not as new SQL columns
     per vendor.
2. **YANG ingestion is mechanical, not hand-transcription.** A one-time
   step per vendor (a script, not a new endpoint): run the vendor's real
   YANG modules — or the O-RAN WG4/WG5/WG10 modules already sitting in
   `specs/` if `conformance_mode=SPEC` — through a real YANG toolchain
   (`pyang`/`pyangbind`) to emit a JSON capability descriptor: IOC class
   names, attribute names + types, RPC/notification names. That JSON is
   what `CMSchemaCache.location` points at (the column already exists
   for exactly this). No one hand-writes a mapping table per vendor;
   the toolchain derives it from the vendor's own YANG source of truth.
3. **A schema check reused by every write path, not written per vendor.**
   Before `send_edit_config` dispatches, look up the target ME's
   `schema_ref` and check the requested `attribute_changes` keys exist
   in that descriptor. This is one small, generic function
   (`_validate_against_schema(db, managed_element_ref, attribute_changes)`
   — the same shape as the existing `_validate_lifecycle_eligibility` in
   DME's `main.py`), not per-vendor branching. An ME with no
   `schema_ref` registered skips the check — same permissive default
   this build already uses everywhere a cross-reference is optional
   (`_validate_job_definition_schema`'s own type-existence check, DME's
   `_validate_lifecycle_eligibility`).
4. **`conformance_mode` decides *which* descriptor(s) the check runs
   against**, making "own/spec/combined" concrete instead of aspirational:
   - `SPEC`: validate strictly against the ingested O-RAN/3GPP YANG
     descriptor only.
   - `OWN`: validate against the vendor's own ingested YANG only — no
     cross-check against the formal spec at all.
   - `COMBINED`: validate against the spec descriptor, but also accept
     any attribute the vendor's own descriptor separately declares (a
     documented, per-vendor augment list) — spec compliance plus
     named extensions, not an unbounded escape hatch.
5. **DME's `/actions` mediation gets the same gate.** `DmeActionRecord`
   already carries the target `managed_element_ref`
   (`dme/app/models.py:167`, forwarded via `_r1.post("/ran-nf-oam/
   config-jobs", ...)` at `dme/app/main.py:745`) — the same schema check
   from step 3 runs there too, so an rApp's inference decision against an
   IOC/attribute a given vendor doesn't support gets a real 4xx from DME
   directly, instead of round-tripping to `ran-nf-oam` and back.

## What onboarding a second vendor looks like once this is built

1. Get the vendor's real YANG bundle (or point at the O-RAN spec
   directly if this vendor is `SPEC`-only).
2. Run the ingestion script once → a JSON capability descriptor, stored
   wherever `CMSchemaCache.location` points (e.g. object storage, or a
   path this build already reads from).
3. `POST /o1-adaptor-endpoints` with `vendorName`, the new
   `conformanceMode`, and a reference to that descriptor — the same call
   that already works today, with two new fields.
4. Nothing else changes in `ran-nf-oam` or `dme` code. The generic check
   from step 3 above reads whichever descriptor that ME's row points to,
   at request time.

Three vendors means three descriptors and three registration calls, not
three code forks.

## What this sketch does *not* solve

Being honest about its ceiling, matching this build's established
"confirmed elision, not an oversight" discipline:

- **Wire-protocol differences.** A vendor whose southbound isn't
  RFC 6241-shaped `edit-config` over HTTP — real NETCONF/SSH via
  `ncclient`, RESTCONF, or a genuinely proprietary API — needs an actual
  new transport adapter, the same "RESTCONF/vendor-API adapters... not
  built this wave" extension point `DME_OWNERSHIP.md` already names.
  This sketch only removes the need for new code on the *data-model*
  axis; a new *transport* still needs a new client module (one per
  transport family, not one per vendor — `ManagedEntity.o1_protocol`
  already exists to select it).
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
