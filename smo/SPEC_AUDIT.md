# Spec audit — `smo/` vs. the formal specs in `specs/`

A different, complementary ground truth from `OPEN_ITEMS.md` section 5's
audits: those compared this build against the O-RAN-SC Repo Blueprint's
*source-code* repos (ADOPT/REFERENCE implementations). This compares
against the *formal specifications* those implementations themselves
build from — 3GPP OpenAPI YAML and O-RAN's real O2IMS information model,
both now living in `specs/` (see `specs/README.md` for the full catalog
and why each file is relevant to which module).

Four modules were audited so far — the ones with the clearest, most
directly relevant spec files already identified in `specs/README.md`.
Not yet audited against a formal spec: DME (ICS's own spec set isn't in
`specs/`), A1 Related (3GPP/O-RAN A1 specs aren't in `specs/` either —
the sim-a1-interface/a1pms source-code audit in `OPEN_ITEMS.md` section 5
remains the only ground truth there), Onboarding/rApp Mgmt (TOSCA/rApp
packaging specs aren't in `specs/`), AI/ML Workflow and RAN Analytics (no
directly relevant O-RAN-SC AI/ML formal spec exists in `specs/` either).

Every finding below was produced by reading the actual spec file(s) and
the actual implementation file(s) side by side — none are guessed or
inferred from memory of the spec family in general. Each is tagged with
a closeability assessment: **small** (a scoped PR, similar to this
session's other closed items), **moderate** (real but larger, possibly
a breaking wire-shape change), or **large/structural** (a genuine
subsystem the spec defines that this build doesn't have at all — almost
always a confirmed, reasonable Phase-1 scope cut, not a bug).

## RAN NF OAM vs. TS28319/TS28111/TS28532/TS28550 + O1NRM YANGs

Specs read: `TS28319_MsacNrm.yaml`, `TS28111_FaultNrm.yaml` +
`FaultNotifications.yaml`, `TS28532_ProvMnS.yaml` +
`HeartbeatNtf.yaml`/`PerfMnS.yaml`/`FileDataReportingMnS.yaml`/
`StreamingDataMnS.yaml`, `TS28550_PerfMeasJobCtrlMnS.yaml`, and the
`O-RAN-WG10-O1NRM-YANGs` files with ManagedElement/managed-function
content.

1. **MSAC gate is a placeholder, not real MSAC RBAC** — large/structural.
   `TS28319_MsacNrm.yaml` models Identity/Role/AccessRule as real NRM
   resources (`Role.accessRulesList`, `AccessRule.dataNodeSelector` +
   `operations[]` + `actions` ALLOW/DENY) — a per-data-node ABAC/RBAC
   engine. `write_configuration_changes`'s `msac_role` is a single
   optional string checked only for presence when `scope=="entire-RAN"`.
   Real MSAC RBAC is a genuine subsystem; the current flag is a
   defensible Phase-1 stand-in, not spec-accurate MSAC. Confirmed
   deliberate elision, not previously so precisely named.
2. **`scope` field name collides with ProvMnS's own `ScopeType`** —
   small/cosmetic. ProvMnS's `scope` means subtree depth
   (`BASE_ONLY`/`BASE_NTH_LEVEL`/...); `WriteConfigRequest.scope` is an
   unrelated fleet-wide-access string. No behavior bug, just a
   misleading name; a rename (e.g. `accessScope`) would remove the
   collision.
3. **createMOI/deleteMOI equivalents entirely absent** — small/groundable.
   `TS28532_ProvMnS.yaml` defines four distinct MOI lifecycle
   operations (PUT create-or-replace, POST-at-parent create, PATCH
   merge/JSON-patch, DELETE). `WriteConfigSubChange.attribute_changes`
   only ever carries attribute modifications — no operation-type field,
   no create/delete path anywhere in `main.py` or `netconf_client.py`.
   Closeable: add an `operation` enum (create/modify/delete) to the
   sub-change model, branch `netconf_client.py` to emit the matching
   `<edit-config operation="...">`.
4. **Flat ref strings instead of DN/typed-identityref addressing** —
   large/structural. Real O1 addressing is hierarchical LDN with typed
   identityrefs (o-du-function, o-cu-up-function, etc., per
   `o-ran-o1-subscription-control-me.yang`'s augment of 3GPP's base
   ManagedElement). `ManagedEntity.managed_element_ref`/
   `managed_function_ref` are opaque flat strings, `entity_type` a free
   string. The 3GPP base ManagedElement/ManagedFunction NRM module
   itself isn't even in `specs/` to fully compare against — the flat
   model is a reasonable, deliberate simplification.
5. **`alarmType` enum missing from the Alarm model** — small/groundable.
   `TS28111_FaultNrm.yaml` requires a closed 11-value `alarmType` enum
   (COMMUNICATIONS_ALARM, QUALITY_OF_SERVICE_ALARM, ..., OTHER) on
   every alarm record. `Alarm`/`ingest_alarm` have no such field at
   all.
6. **`perceivedSeverity` not a closed enum; `ackUserId` and
   `alarmChangedTime` missing** — small, each independently groundable.
   Spec: `PerceivedSeverity` is a closed 6-value enum; ack-state-change
   notifications require `ackUserId`; `AlarmRecord` tracks
   `alarmChangedTime` distinct from raised/cleared. Impl: `severity` is
   a free string (deliberately, per its own docstring); `PATCH
   /alarms/{id}/ack` never records who acknowledged (no `ack_user_id`
   column); no "last changed" timestamp exists.
7. **PM subscription missing `granularityPeriod`** — small/groundable
   if it matters. `subscribe_pm`'s own docstring already states it's
   "a DME-producer registration wrapper, NOT a clause-8 API call," so
   most of `TS28550_PerfMeasJobCtrlMnS.yaml`'s real job-control fields
   (schedule, priority, multi-instance, reportingPeriod) are a
   confirmed deliberate scope cut. `granularityPeriod` (the sampling
   interval) is the one exception worth flagging — needed by any real
   PM subscription regardless of wrapper shape, and fully absent.
8. **File/streaming transport machinery** (`FileDataReportingMnS`,
   `StreamingDataMnS`) — confirmed deliberate, large/structural if ever
   built; `southbound_engine` is just a chosen label today, consistent
   with (7)'s documented wrapper framing.
9. **`HeartbeatNtf`'s direction is the reverse of `endpoint_heartbeat`**
   — not a gap. The spec's `NotifyHeartbeat` is a producer→consumer
   push keeping an MnS *subscription* alive; `endpoint_heartbeat` is
   the reverse (an O1 adaptor endpoint pinging *in* to age its own
   registry entry). Two unrelated, non-conflicting mechanisms that
   happen to share the word "heartbeat" — ran-nf-oam never claimed
   `HeartbeatNtf` compliance.
10. The `WriteConfigJob` aggregation layer and `PARTIAL_SUCCESS` state
    have no spec equivalent (ProvMnS is per-resource synchronous, no
    "job" resource) — confirmed deliberate, already honestly documented
    in `statemachine.py`'s own comment. Not a delta worth closing.

## FOCOM vs. the real O2IMS spec (`o-cloud-im/resources/`)

All six spec files are **data-model only** (`paths: {}`) — O-RAN's WG6
O2-IM spec defines schemas, not HTTP operations; route/pagination
comparisons below are necessarily schema-level only.

1. **`ResourceType` missing fields** — small. Spec requires
   `alarmDictionaryId`, `performanceDictionaryId` (dictionary refs),
   `resourceKind` (enum UNDEFINED/PHYSICAL/LOGICAL), `resourceClass`
   (enum UNDEFINED/COMPUTE/NETWORKING/STORAGE), `extensions`. FOCOM's
   model has none of these five nullable fields.
2. **`ResourcePool` missing `oCloudSiteId` + required `resources`
   array** — small-medium. Spec's pool references an `OCloudSite`
   concept FOCOM has no equivalent for at all, and requires an inline
   `resources` array (FOCOM instead exposes it via the separate `GET
   /resource-pools/{id}/resources` route — a reasonable Phase-1 shape
   choice, but a real deviation from the spec's required inline field).
3. **`Resource` missing `globalAssetId`/`tags`/`groups`/`extensions`**
   — small. `elements` (spec: self-referential array, parent holds
   children) vs. `parent_id` (impl: FK, child points to parent) is a
   reasonable, deliberate simplification of the same concept, not a
   bug.
4. **`DeploymentManager` missing three required fields** — small.
   Spec requires `supportedLocations`, `capabilities`, `capacity`
   (arrays); FOCOM's model has neither. `service_uri` is a reasonable
   rename of the spec's `deploymentManagementServicesEndpoint`.
5. **`InventorySubscription` field-name mismatch** — small. Spec's
   field is `callback`, not `callbackUri`; spec also has
   `consumerSubscriptionId` passthrough FOCOM lacks entirely. FOCOM's
   own `resourceTypeId` filter is a reasonable narrower substitute for
   the spec's free-text `filter`.
6. **Alarms/Performance are drastically thinner than the spec's real
   FCAPS model** — large/structural, confirmed deliberate (no real
   hardware telemetry source exists in this build). Spec's
   `AlarmEventRecord` requires 9+ fields including an 11-value X.733
   `eventType` enum and a full `AlarmSubscription`/`AlarmList`
   notify path; FOCOM's `OCloudAlarm` has three fields and no
   subscribe/notify mechanism at all. Same gap shape for Performance
   (spec: job/dictionary/state-machine model; impl: flat metric
   ingest-and-query).
7. **Whole resource categories with zero FOCOM equivalent** —
   large/structural, consistent with the module's own documented
   single-degenerate-cluster Phase-1 scope: `ProvisioningRequest`'s
   real template-driven workflow (`ORAN.O2ims.Provisioning.yaml`),
   `ArtifactResourceType`/`ArtifactResource` software catalog
   (`Artifacts.yaml`), `NodeCluster`/`ClusterResource`
   (`Cluster.yaml`), `Gateway`/`SiteNetwork`/network-fabric schemas
   (`Infrastructure.yaml`).
8. **`GET /inventory`'s response shape doesn't correspond to any real
   O2IMS schema** — medium, real consumer-breaking cost. The spec's
   aggregate root is `OCloud` (`oCloudId`, `resourceTypes[]`,
   `locations[]`, `deploymentManagers[]`, ...); FOCOM's `/inventory`
   returns an ad hoc `{clusterId, resourcePools:[...]}` shape that
   matches neither `OCloud` nor any wrapped list. Reshaping toward
   `OCloud`'s field names is plausible but touches NFO's real
   Instantiate caller contract (`main.py`'s own docstring notes this
   explicitly), so it's a rename with a real cost, not just a model
   add.
9. Minor footnote: `ResourceType` is `readOnly: true` throughout the
   spec (discovered, not created), but `provision_resource` silently
   auto-registers an unrecognized `resourceTypeId` on demand — a small
   spec deviation, not urgent.

## Policy Mgmt vs. TS28312 IntentNrm

**Important framing correction the audit itself surfaced**: the "needs
a design decision" language for Intent-to-RMIH matching quoted from
`OPEN_ITEMS.md` section 1 is *stale* — a later pass (documented in the
"Closed" section) already implemented capability-based push matching
(`create_intent`/`_matching_rmihs` in `main.py`). The real open question
this audit answers is whether that implementation is spec-grounded.

**The spec doesn't validate the mechanism actually built, but it does
hand back a different, citable answer.** `TS28312_IntentNrm.yaml` is
`paths: {}` — pure data model, no subscribe/notify operation, no
matching algorithm defined anywhere. But the NRM containment model
(`IntentHandlingFunction-Single` *contains* `Intent: Intent-Multiple`
as a named child) implies the real spec's answer is **consumer-side
selection by LDN** — the MnS consumer picks and addresses a specific
already-chosen RMIH when creating an Intent — not the producer-side
push-after-creation dispatch this build built. That's a real,
architecturally different, spec-grounded alternative, not just more
abstraction — worth a design note citing it specifically before
treating the current mechanism as final.

Concrete deltas, independent of the architecture question above:

1. **`intentHandlingScope` should be a closed 2-value enum (RAN/CN),
   currently untyped JSON and never set or read** — small, closeable
   now.
2. **Matching uses the wrong field** — moderate, breaking wire-shape
   change. Spec's real matching field is
   `IntentHandlingCapability.supportedExpectationObjectType` (closed
   4-value enum: RAN_SUBNETWORK/EDGE_SERVICE_SUPPORT/
   5GC_SUBNETWORK/RADIO_SERVICE). Impl matches on an invented
   free-form `intentType` string with no shared vocabulary with the
   spec at all.
3. **`intentType` is stored into `intent_mgmt_purpose`, but
   `intentMgmtPurpose` is a workflow-procedure enum in the spec**
   (FEASIBILITYCHECK, FULFILMENT_WITHOUT_NEGOTIATION, ...), not a
   domain/object-type discriminator — the field driving matching is
   semantically the wrong one per the spec. Tied to fixing (2).
4. **Missing required `Intent` fields**: `intentReportControl`,
   `intentReportReference` (no report-subscription config exists at
   all — `observationPeriod`, `expectedReportTypes`,
   `reportRecipientAddress`); `userLabel` exists on the model but is
   never settable via the create request — moderate, a real feature
   gap, not just naming.
5. **No `DELETE /intents/{id}`** — small, easy.
6. **`IntentReport` only covers 2 of the spec's 6 report kinds**
   (fulfilment, conflict — missing feasibility-check, exploration,
   negotiation, decomposition), and there's no GET to read reports
   back — moderate; reasonable to leave descoped given
   `intentExpectations` is already an acknowledged opaque JSON blob.

## SME vs. the real CAPIF core (`capifcore/`) — manual review, not
formal-spec-file-based (no CAPIF OpenAPI spec file lives in `specs/`;
this uses the real Go source already cloned for section 5's audit, read
more closely for security/trust-model detail than that pass went)

1. **Invoker onboarding has the trust direction backwards** — real,
   previously undocumented gap, moderate/real security-model
   difference. The real CAPIF `APIInvokerEnrolmentDetails`/
   `OnboardingInformation` schema is public-key-based: the client
   supplies `apiInvokerPublicKey`; the CAPIF core function *generates*
   both `apiInvokerId` and `onboardingSecret` and returns them —
   `apiInvokerId` "shall not be present" in the client's onboarding
   request. This build's `InvokerRegistrationRequest` instead takes
   both `apiInvokerId` and `onboardingSecret` as **client-supplied**
   input — a self-asserted identity and a client-chosen secret, not a
   server-issued one. Weaker trust model than the real spec, not
   currently documented anywhere as a deliberate choice.
2. **The real CAPIF core's "Trusted Invokers" security-context
   subsystem has no equivalent at all** — large/structural, previously
   undocumented. `capifcore/internal/securityservice/security.go`
   implements a second, separate real mechanism beyond OAuth2 token
   issuance: `PUT`/`GET`/`DELETE /trusted-invokers/{apiInvokerId}` plus
   revocation (`POST .../delete`), managing per-AEF+API
   `SecurityInfo` (authentication/authorization info) that a real AEF
   (resource server) would consult. SME's `/oauth2/introspect` (RFC
   7662) is a related but distinct, honest standards-based mechanism
   for a different purpose (R1 Termination's own gateway check) — it
   doesn't cover this per-AEF trusted-invoker registry at all. Already
   adjacent to the documented "per-scope AEF/API validation... stays
   out of scope" elision, but that line doesn't capture that a whole
   separate real subsystem exists here, not just a missing check.
3. **VES-based heartbeat (`oam/code/client-scripts-ves-v7/
   sendVesHeartbeat.py`) confirmed NOT a gap** — this build's
   endpoint-registry heartbeat pattern (a lightweight REST POST) is a
   reasonable, deliberately lighter analog of the real, much heavier
   VES Event Listener protocol (external collector, structured
   `commonEventHeader`), not a divergence worth closing — adopting
   real VES would be a disproportionate new subsystem for Phase 1,
   consistent with the "ADOPT repos stay pattern references only"
   philosophy already established elsewhere.

## What's genuinely closeable now (small, scoped, non-breaking)

In priority order — these don't touch any established wire contract a
real caller (NFO, another module, a test) depends on, so each is a
same-shape PR like this session's others:

1. ~~RAN NF OAM: add `alarm_type` (11-value enum) to `Alarm`.~~ —
   **closed** (`OPEN_ITEMS.md`'s pass-history log).
2. ~~RAN NF OAM: constrain `severity` intent aside — add `ack_user_id`
   and `changed_at` to `Alarm`/the ack-state route.~~ — **closed**
   (same pass as item 1).
3. ~~RAN NF OAM: add an `operation` enum (create/modify/delete) to
   `WriteConfigSubChange` and wire `netconf_client.py`'s RPC builder to
   emit it.~~ — **closed**. Grounded in RFC 6241 section 7.2's real
   edit-config `operation` attribute (merge/replace/create/delete/
   remove — this build's actually-implemented southbound protocol)
   rather than `TS28532_ProvMnS.yaml`'s HTTP-verb framing; defaults to
   `"merge"` so every existing caller is unaffected. Surfaced a real
   bug fixed in the same pass: `mock-o1-adaptor`'s `edit_config`
   handler rejected any empty `attribute_changes` payload
   unconditionally, which would have wrongly rejected a legitimate
   delete (`OPEN_ITEMS.md`'s pass-history log).
4. ~~RAN NF OAM: add `granularity_period` to `PMSubscription`.~~ —
   **closed** (`OPEN_ITEMS.md`'s pass-history log).
5. ~~Policy Mgmt: constrain `intent_handling_scope` to the real 2-value
   enum and actually use it as a pre-filter.~~ — **closed**
   (`OPEN_ITEMS.md`'s pass-history log).
6. ~~Policy Mgmt: add `DELETE /intents/{id}`.~~ — **closed** (same
   pass as item 5; cascades to `IntentReport`).
7. ~~FOCOM: add the five missing `ResourceType` fields, three missing
   `DeploymentManager` fields, and `Resource`'s
   `tags`/`groups`/`globalAssetId` as nullable columns.~~ — **closed**
   (`OPEN_ITEMS.md`'s pass-history log; `resourceKind`/`resourceClass`
   got real `CHECK` constraints too).
8. ~~FOCOM: rename `InventorySubscription`'s wire field `callbackUri` →
   `callback` (or explicitly document the deviation) and add
   `consumerSubscriptionId` passthrough.~~ — **closed**
   (`OPEN_ITEMS.md`'s pass-history log). This was the last item on
   this list — every small/scoped, non-breaking spec gap identified in
   this pass is now closed.

Moderate/breaking-shape items (worth a deliberate follow-up pass, not
a quick fix, since each changes a request/response contract a real
caller or test already depends on): Policy Mgmt's matching-field
rename (`intentType` → `supportedExpectationObjectType`), FOCOM's
`GET /inventory` reshape toward `OCloud` (touches NFO's caller
contract), SME's invoker-onboarding trust-model fix (would need a real
design decision on whether to actually flip to server-generated
identity/secret, a bigger behavior change than this session's usual
scoped fixes).

Large/structural items are, on inspection, essentially all *confirmed*
Phase-1 scope cuts rather than newly discovered bugs — real subsystems
(MSAC RBAC, DN/typed addressing, file/streaming transport, FOCOM's
Provisioning/Artifacts/Cluster/Infrastructure categories, CAPIF's
Trusted Invokers registry) this build was never going to build in
Phase 1, now named precisely against their real spec citation instead
of described only in general terms.
