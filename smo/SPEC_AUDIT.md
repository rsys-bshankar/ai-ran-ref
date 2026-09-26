# Spec audit — `smo/` vs. the formal specs in `specs/`

A different, complementary ground truth from `OPEN_ITEMS.md` section 5's
audits: those compared this build against the O-RAN-SC Repo Blueprint's
*source-code* repos (ADOPT/REFERENCE implementations). This compares
against the *formal specifications* those implementations themselves
build from — 3GPP OpenAPI YAML and O-RAN's real O2IMS information model,
both now living in `specs/` (see `specs/README.md` for the full catalog
and why each file is relevant to which module).

Six modules have been audited so far: the four with the clearest, most
directly relevant spec files already identified in `specs/README.md`
(RAN NF OAM, FOCOM, Policy Mgmt, SME), plus AI/ML Workflow and RAN
Analytics — `specs/README.md`'s own earlier claim that no directly
relevant O-RAN-SC AI/ML formal spec exists in `specs/` was stale:
`TS28105_AiMlNrm.yaml` (AI/ML NRM) and `TS28104_MdaNrm.yaml`/
`TS28104_MdaReport.yaml` (MDA NRM) were already present under
`5G_APIs/`, just never cataloged there. Not yet audited against a
formal spec, because no relevant spec file exists in `specs/` at all:
DME (ICS's own spec set isn't there), A1 Related (3GPP/O-RAN A1 specs
aren't there either — the `sim-a1-interface`/`a1pms` source-code audit
in `OPEN_ITEMS.md` section 5 remains the only ground truth there), and
Onboarding/rApp Mgmt (TOSCA/rApp packaging specs aren't there).

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

*(This module was renamed `intent-service/` in Wave 1 of the AI Platform
Service Decomposition — see `docs/ownership/INTENT_SERVICE_OWNERSHIP.md`.
Findings below are unchanged and still accurate; only the directory and
service name changed, not the code.)*

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
2. ~~**The real CAPIF core's "Trusted Invokers" security-context
   subsystem has no equivalent at all**~~ — **closed**, per explicit
   user direction to build the real subsystem rather than leave it
   documented. Added the real `PUT`/`GET`/`DELETE
   /trusted-invokers/{apiInvokerId}` plus revocation (`POST
   .../delete`), matching `capifcore/internal/securityservice/
   security.go`'s own routes and validation (invoker-registration gate
   on `PUT`, `notificationDestination`/`securityInfo`/
   `prefSecurityMethods` required, `GET`'s real authenticationInfo/
   authorizationInfo redaction by default, revocation's real per-entry
   removal with whole-record cleanup once empty). `PrepareNewSecurity
   Context`'s own real cross-check against a published AEF's declared
   security methods has no equivalent data source in this build (no
   per-AEF security-method catalog was ever modeled) — adapted
   honestly: `selSecurityMethod` is the invoker's own first declared
   preference, not a fabricated AEF-side match. Confirmed by inspection
   that CAPIF core's own token-issuance path
   (`PostSecuritiesSecurityIdToken`) never reads `trustedInvokers` at
   all — this is a standalone registry a real AEF would consult
   directly, so no existing SME route needed rewiring. New
   `TrustedInvoker` model (`security_info` as one JSON list per
   invoker, the same "flatten to what this build's own identity model
   needs" adaptation already used for `aefProfiles`/
   `DMEType.collection_spec`), 17 new unit tests, a new
   `DEMO_RUNBOOK.md` section, and a matching integration-test step.
   Verified against a real local Postgres 16 instance (59 tables, up
   from 58, 0 mismatches; a manual insert round-tripped the new JSONB
   `security_info` column). `docs/openapi/sme.json` regenerated.
3. **VES-based heartbeat (`oam/code/client-scripts-ves-v7/
   sendVesHeartbeat.py`) confirmed NOT a gap** — this build's
   endpoint-registry heartbeat pattern (a lightweight REST POST) is a
   reasonable, deliberately lighter analog of the real, much heavier
   VES Event Listener protocol (external collector, structured
   `commonEventHeader`), not a divergence worth closing — adopting
   real VES would be a disproportionate new subsystem for Phase 1,
   consistent with the "ADOPT repos stay pattern references only"
   philosophy already established elsewhere.

## AI/ML Workflow vs. TS28105 AI/ML NRM

*(This module was split into `aimgf/`, `mlmr/`, `mllf/` in Wave 1 of the
AI Platform Service Decomposition — see
`docs/architecture/SERVICE_OWNERSHIP_MATRIX.md` and
`docs/ownership/{AIMGF,MLMR,MLLF}_OWNERSHIP.md`. This is the deliberate
reversal of this section's own "confirmed flat-shape" finding below:
the split moves toward TS28.105's real NRM service boundaries — AIMgF,
MLMR, MLLF are its own real function names — though not yet its full
containment-tree/DN-addressing/FL-RL shape, which remains future work.
Findings below are otherwise unchanged and still accurate.)*

Spec read: `TS28105_AiMlNrm.yaml` (`paths: {}`, data-model only, same
shape as the O2IMS spec below). Previously listed as "not yet audited"
— `specs/README.md`'s claim that no directly relevant O-RAN-SC AI/ML
formal spec exists in `specs/` was stale; this file was already present
under `5G_APIs/`, just never cataloged there.

**Framing**: TS28.105 models AI/ML management as a full NRM containment
tree — `MLTrainingFunction`/`MLTestingFunction`/`MLModelRepository`/
`MLUpdateFunction`/`AIMLInferenceFunction`/
`AIMLInferenceEmulationFunction`, each containing typed
Request/Process/Report child IOCs addressed by DN, with real FL/RL
semantics (`FLRequirement`/`RLRequirement`/`FLParticipationInfo`/
`SupportedLearningTechnology`) and a `ThresholdMonitor`-integrated
`AIMLManagementPolicy`. This build instead targets the O-RAN-SC
nonrtric `aiml-fw`/`trainingmgr` reference architecture — already
confirmed and audited in `OPEN_ITEMS.md` section 5 — a flatter REST
job-manager shape (`AIMLModel` + `TrainingJob` + `InferenceJob`, no
Request/Process/Report triplet, no DN addressing, no FL/RL modeling). A
confirmed, deliberate architecture choice, the same category as
FOCOM's O2IMS mismatch below, not a bug.

1. **Whole NRM containment tree absent** — large/structural, confirmed
   deliberate. No `MLTestingFunction`/`MLTestingRequest`/
   `MLTestingReport`, no `MLUpdateFunction`/`MLUpdateRequest`/
   `MLUpdateProcess`/`MLUpdateReport`, no `MLModelLoadingRequest`/
   `MLModelLoadingProcess`/`MLModelLoadingPolicy`, no distinct
   `AIMLInferenceReport` resource (`InferenceJob` is a much simpler
   analog with no `potentialImpactInfo`/`managedActivationScope`), no
   DN/typed addressing anywhere. This build targets `aiml-fw`'s own
   reference shape instead.
2. **No FL/RL modeling at all** — large/structural, confirmed
   deliberate. `FLRequirement`/`FLParticipationInfo`/`RLRequirement`/
   `SupportedLearningTechnology`/`ClusteringCriteria` have zero
   equivalent — consistent with no distributed-training-orchestration
   subsystem existing anywhere else in this build either.
3. **`MLModelCoordinationGroup.memberMLModelRefList` requires
   `minItems: 2`** — not a gap, a confirmation. This is the exact same
   constraint this build's own migration independently enforces
   (`array_length(member_model_ids, 1) >= 2`), closed with a
   `COORDINATION_GROUP_TOO_SMALL` pre-validation in the same pass that
   demoed SA SMOS's coordination-group remedial action. Retroactively
   confirms that fix was spec-grounded, not just an internal
   DB-constraint choice invented from nothing.
4. ~~**`mLTrainingType` computed internally but never stored or
   exposed**~~ — **closed.** The spec's real, closed 4-value enum
   (`INITIAL_TRAINING`/`PRE_SPECIALISED_TRAINING`/`RE_TRAINING`/
   `FINE_TUNING`) on both `MLModel` and `MLTrainingRequest`.
   `request_training` already computed this exact
   INITIAL_TRAINING-vs-RE_TRAINING distinction internally (as a
   `ModelEvent.TRAIN`/`RETRAIN` FSM choice) but never stored or
   returned it. Closed with a new `ml_training_type` column, computed
   in both `request_training` and `_trigger_group_retrain` (always
   `RE_TRAINING` there — `RETRAIN` is the only legal transition it ever
   fires), returned from both `POST /training-jobs`'s status read and
   `GET /training-jobs/{id}/status`. `PRE_SPECIALISED_TRAINING`/
   `FINE_TUNING` have no equivalent concept in this build, so only two
   of the spec's four values are ever produced — an honest partial
   mapping, not a fabricated one. Verified against a real local
   Postgres 16 instance (the new `CHECK` constraint accepts all four
   spec values, live-exercised for both produced ones) and the full
   unit/integration suites.
5. **`requestStatus`'s real 6-value enum
   (NOT_STARTED/IN_PROGRESS/SUSPENDED/FINISHED/CANCELLED/CANCELLING) vs.
   this build's own `TrainingJob.status`
   (PENDING/RUNNING/COMPLETED/FAILED/CANCELLED)** — moderate/breaking,
   not closed. A vocabulary mismatch only, not a functional gap (same
   shape as RAN NF OAM's `scope`/`ScopeType` naming collision below),
   but renaming an established, widely-depended-on field's values would
   be a real breaking change across every existing caller — left for a
   deliberate follow-up pass, not a quick fix.
6. **No `cancelRequest`/`suspendRequest` in-place flag mechanism** —
   moderate, not closed. This build's own `cancel_training` is a hard
   `DELETE`, and there's no suspend concept at all. A real, closeable
   feature gap, but reshaping the training-job lifecycle's own
   established contract is a bigger change than this pass's usual
   scoped fixes.
7. **`AIMLManagementPolicy`/`ThresholdMonitorNrm` integration confirmed
   NOT a gap** — this build's own `MLMFSubscription.guard_kpi_floor` is
   a real, working, functionally equivalent threshold mechanism
   already, just under this build's own `aiml-fw`-derived name/shape
   rather than TS28.105's `ThresholdMonitor`.

## RAN Analytics vs. TS28104 MDA NRM

Spec read: `TS28104_MdaNrm.yaml` + `TS28104_MdaReport.yaml` (`paths: {}`,
data-model only). Same stale-catalog correction as AI/ML Workflow above
— both files were already present under `5G_APIs/`.

**Framing**: TS28.104 models MDA (Management Data Analytics) as a
request-response NRM: a consumer creates an `MDARequest`
(`requestedMDAOutputs`, `analyticsScope`, threshold-based conditional
reporting via `ThresholdInfo`), and an `MDAFunction` generates
`MDAReport`s in response, addressed by DN inside a
`SubNetwork`/`ManagedElement` containment tree. This build instead
implements the O-RAN-SC nonrtric `aiml-fw-apm` reference's actual shape
— already audited in `OPEN_ITEMS.md` section 5 — proactive
producer-push, not consumer-request: a producer registers itself plus
its `analytics_type`, publishes reports on its own initiative, and a
separate subscriber list receives them. A confirmed, deliberate
architecture choice, not a bug.

1. **Whole `MDARequest`-driven request-response model absent, replaced
   by proactive producer-push** — large/structural, confirmed
   deliberate. Matches `aiml-fw-apm`'s own reference shape, not
   TS28.104's NRM.
2. **`analytics_type` is a free string; the spec defines a real, closed
   24-value `MDAType` enum** (`COVERAGE_ANALYTICS_COVERAGE_PROBLEM_
   ANALYSIS`, `MOBILITY_MANAGEMENT_ANALYTICS_MOBILITY_PERFORMANCE_
   ANALYSIS`, etc.) — moderate/breaking, not closed. Constraining it
   would reject whatever `analytics_type` strings any existing caller
   (demo, tests, other modules) already uses — not yet audited for real
   usage before attempting this, so left open rather than guessed at.
3. **No `ThresholdInfo`-based conditional reporting** (`UP`/`DOWN`/
   `UP_AND_DOWN` + hysteresis) — moderate, real feature gap, not closed.
   Every report always fires regardless of value; this build's own
   `MLMFSubscription.guard_kpi_floor` (AI/ML Workflow, above) is a
   directly analogous mechanism already implemented elsewhere in this
   same codebase this module could crib from. Real and scoped, just not
   done this pass.
4. **`scope` is an opaque JSON blob, not the spec's real structured
   `AnalyticsScopeType`** (a `managedEntitiesScope` DN list or
   `areaScope`) — small/cosmetic, consistent with this build's already-
   declared no-real-DN-addressing elision (RAN NF OAM's flat-string
   addressing, above).
5. **`reportingMethod`'s FILE/STREAMING options confirmed NOT a gap** —
   consistent with the already-declared file/streaming transport
   elision (RAN NF OAM's `FileDataReportingMnS`/`StreamingDataMnS`
   finding, above); this build's `notification_destination` is honestly
   NOTIFICATION-only.

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
   this list from the first four modules audited — every small/scoped,
   non-breaking spec gap identified in that pass was closed.
9. ~~AI/ML Workflow: add `mLTrainingType` (INITIAL_TRAINING/
   PRE_SPECIALISED_TRAINING/RE_TRAINING/FINE_TUNING) to `TrainingJob`,
   computed from `request_training`'s own already-existing
   TRAIN-vs-RETRAIN FSM-event choice.~~ — **closed**
   (`OPEN_ITEMS.md`'s pass-history log). This was the one small,
   non-breaking, additive item found auditing AI/ML Workflow and RAN
   Analytics — everything else found there is either large/structural
   (a confirmed architecture choice: this build targets `aiml-fw`/
   `aiml-fw-apm`'s own O-RAN-SC reference shape, not TS28.105/TS28.104's
   3GPP NRM containment-tree model) or moderate/breaking (RAN
   Analytics's `analytics_type` enum constraint, its missing
   threshold-based conditional reporting, and AI/ML Workflow's
   `requestStatus` vocabulary and cancel/suspend-flag gaps) — see those
   two modules' own sections above.

Moderate/breaking-shape items (worth a deliberate follow-up pass, not
a quick fix, since each changes a request/response contract a real
caller or test already depends on):

1. ~~Policy Mgmt's matching-field rename (`intentType` →
   `supportedExpectationObjectType`).~~ — **closed**
   (`OPEN_ITEMS.md`'s pass-history log). Matching now reads
   `TS28312_IntentNrm.yaml`'s real field, each expectation's own
   `expectationObject.objectType`, straight out of the already-opaque
   `expectations` list, against each RMIH's declared
   `supportedExpectationObjectType`; `intentMgmtPurpose` (previously
   conflated with the matching field) is now a real, independently
   settable field with the spec's own default and a real Postgres
   `CHECK` constraint. A repo-wide grep confirmed no other module ever
   called these routes with the old field names, so the blast radius
   stayed entirely inside `policy-mgmt/`.
2. ~~FOCOM's `GET /inventory` reshape toward `OCloud` (touches NFO's
   caller contract).~~ — **closed** (`OPEN_ITEMS.md`'s pass-history
   log). `oCloudId`/`name`/`description`/`resourceTypes`/
   `deploymentManagers` now come from FOCOM's own real topology;
   `locations`/`oCloudSites` are honestly empty (no `OCloudSite`/
   `Location` concept exists) rather than fabricated. NFO's real
   `Instantiate` caller — the one real cross-module dependency —
   was checked first and updated to read `oCloudId` in place of the
   old `clusterId`, with the exact same graceful fallback kept.
3. ~~SME's invoker-onboarding trust-model fix (would need a real design
   decision on whether to actually flip to server-generated
   identity/secret, a bigger behavior change than this session's usual
   scoped fixes).~~ — **closed**, per explicit user direction to
   implement it (`OPEN_ITEMS.md`'s pass-history log). Flipped:
   `InvokerRegistrationRequest` now takes only `apiInvokerPublicKey`;
   `register_invoker` mints a real server-side `apiInvokerId` and
   random `onboardingSecret` and returns both, matching the real
   CAPIF core's own onboarding direction. Item 2 above (the real
   CAPIF core's separate "Trusted Invokers" security-context
   registry) remains open — a genuine additional subsystem, not just
   this onboarding-flow gap.

This closes every item on this "Moderate/breaking-shape items" list.

Large/structural items are, on inspection, essentially all *confirmed*
Phase-1 scope cuts rather than newly discovered bugs — real subsystems
(MSAC RBAC, DN/typed addressing, file/streaming transport, FOCOM's
Provisioning/Artifacts/Cluster/Infrastructure categories, CAPIF's
Trusted Invokers registry) this build was never going to build in
Phase 1, now named precisely against their real spec citation instead
of described only in general terms.

## Large/structural items: demo-relevance triage

Per explicit direction: for each large/structural item above, whether
it's worth reconsidering before `DEMO_RUNBOOK.md`'s pilot demo, or is
genuinely out of scope for it. Grounded directly in what the runbook's
own steps actually call — not a general risk assessment. The runbook
exercises Onboarding → rApp Mgmt → NFO → FOCOM (`GET /inventory`,
read-only) → SME (provider/invoker registration, OAuth2 token, publish
service API) → DME (optional producer registration) → rApp Mgmt
(performance report, terminate, delete). RAN NF OAM and Intent Service
(then named Policy Mgmt) were never called anywhere in the runbook when
this triage was written; Intent Service since gained its own *optional*
demo section (§10, "Demo Phase D") — see the correction in the
paragraph right after this numbered list. RAN NF OAM remains uncalled.

1. **RAN NF OAM: MSAC RBAC placeholder.** Out of scope. The gated
   route (`WriteConfigurationChanges` / `POST /config-jobs`) never
   appears in the runbook — RAN NF OAM isn't touched at all.
2. **RAN NF OAM: flat-string addressing instead of DN/typed
   identityrefs.** Out of scope, same reason — RAN NF OAM isn't in the
   demo path.
3. **RAN NF OAM: file/streaming transport machinery
   (`FileDataReportingMnS`/`StreamingDataMnS`).** Out of scope —
   `subscribe_pm` (the one route this would affect) isn't called
   either; the runbook's "operate" step is rApp Mgmt's own
   `performance` route, not a real PM subscription.
4. **FOCOM: FCAPS (alarm/performance) depth.** Out of scope. FOCOM
   appears exactly once, at step 3, as a single read-only
   `GET /inventory` call sourcing a cluster for NFO's `Instantiate` —
   no alarm or performance route is ever called.
   **Update, per later explicit user direction to build it anyway:
   closed** — `DEMO_RUNBOOK.md`'s FOCOM section now includes real
   alarm ingest/query and a performance query (empty in a fresh stack,
   honestly disclosed — no ingest route exists in this build).
5. **FOCOM: whole resource categories absent (`ProvisioningRequest`,
   `ArtifactResourceType`/`ArtifactResource`, `NodeCluster`,
   `Gateway`/`SiteNetwork`).** Out of scope, same reason as (4) — the
   demo never exercises FOCOM beyond that one inventory read.
6. **SME: the real CAPIF core's "Trusted Invokers" security-context
   subsystem has no equivalent.** **Worth reconsidering** — this is
   the one item the demo actually walks through live. Step 4's own
   invoker-registration call has the client self-assert
   `apiInvokerId: 'hello-world-rapp'` and choose its own
   `onboardingSecret` — exactly the weaker trust model the adjacent
   moderate-tier finding above (SME item 1: "invoker onboarding has
   the trust direction backwards") already names, and exactly what a
   real Trusted Invokers registry (`PUT`/`GET`/`DELETE
   /trusted-invokers/{apiInvokerId}` + revocation) would exist to
   check against. Building the real subsystem before the demo is
   disproportionate — it's a genuine new subsystem, not a scoped fix,
   the same assessment the audit already gave it. But because a
   technically literate reviewer watching this exact step could
   reasonably ask "what stops a different rApp from registering with
   this same invoker ID," this is worth a one-line disclosure in the
   live walkthrough ("self-asserted identity in Phase 1; the real
   CAPIF core issues these server-side") rather than staying a silent
   gap, and a named line on the platform roadmap — not a demo
   blocker, but the one item here with real audience-facing exposure.
   **Update, per later explicit user direction to build it anyway:
   closed** — see the struck-through item 2 above; the real
   `PUT`/`GET`/`DELETE /trusted-invokers/{apiInvokerId}` registry now
   exists and has its own `DEMO_RUNBOOK.md` section.

Not on the large/structural list, but adjacent and worth naming for
the same reason: Intent Service's own architecture question (§ above,
under its former name Policy Mgmt — whether Intent-to-RMIH matching
should be consumer-side LDN selection per the spec's NRM containment
model, rather than this build's producer-side push). **Correction**:
this was originally assessed as moot for the demo because `CreateIntent`/
`RegisterIntentHandlingFunction` were never called in the runbook — that
was true when this line was written, but a later pass (Demo Phase D,
`OPEN_ITEMS.md`'s pass-history log) added exactly that walkthrough to
`DEMO_RUNBOOK.md` §10. This question is therefore demo-relevant after
all, the same category as the SME Trusted Invokers item above: worth a
one-line disclosure in the live walkthrough rather than a silent
assumption, and squarely on the table for Intent Service's own Wave 3
contract design (see `docs/ownership/INTENT_SERVICE_OWNERSHIP.md`) to
resolve properly rather than carry forward again.

7. **AI/ML Workflow's and RAN Analytics's own large/structural items
   (whole TS28.105/TS28.104 NRM containment trees, FL/RL modeling,
   MDARequest-driven request-response reporting).** Out of scope for
   the same reason as everywhere else on this list — `DEMO_RUNBOOK.md`
   exercises AI/ML Workflow's own `aiml-fw`-shaped routes (register,
   train, upload/download an artifact, advance the lifecycle, feature
   groups, a coordination-group remedial action) and RAN Analytics's
   own `aiml-fw-apm`-shaped routes (register a producer, subscribe,
   publish a report) — none of which touch TS28.105/TS28.104's real NRM
   surface at all, since this build never claimed to implement that
   surface in the first place. No reconsideration needed.
