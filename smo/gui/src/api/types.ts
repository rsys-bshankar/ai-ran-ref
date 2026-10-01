// Response shapes, taken from each module's own view functions
// (smo/<module>/app/main.py) — docs/openapi/<module>.json is the contract.

export interface Me { username: string; role: "viewer" | "operator" | "admin"; csrfToken?: string }

export interface ModuleStatus { module: string; healthy: boolean; latencyMs: number; statusCode: number | null; error: string | null }
export interface ModulesStatus { checkedAt: string; modules: ModuleStatus[] }

// ---- Onboarding / rApp Management
export interface Package {
  packageId: string; name: string; version: string; vendor: string | null; applicationType: string;
  state: string; toscaEntryDefinitions: string | null; signatureVerified: boolean; nfDeploymentDescriptorId: string | null;
  aiCapabilities: Record<string, unknown> | null;
  // Real ASD schema fields (asd_types.yaml's tosca.nodes.asd node type,
  // grounded against nonrtric-plt-rappmanager's own sample CSARs) — null
  // for a package whose ASD doesn't declare them (e.g. built before this
  // pass, or a synthetic test fixture).
  descriptorId: string | null; descriptorInvariantId: string | null;
  descriptorVersion: string | null; schemaVersion: string | null;
  // Real CSAR-bundled Files/Sme/providers + Files/Sme/serviceapis
  // declarations (HISTORY.md §7's Onboarding/rApp Mgmt finding 3) — null
  // for a package whose CSAR declares neither directory. Registered per
  // instance at bootstrap-complete, not here at onboarding time.
  smeDeclarations: { providers: Record<string, unknown>[]; serviceApis: Record<string, unknown>[] } | null;
}
export interface InstanceSummary {
  instanceId: string; packageId: string; state: string;
  // HISTORY.md OI-6.3: fixed at onboarding (CreateInstance), SHADOW by default.
  autonomyMode: string;
}
export interface Instance extends InstanceSummary {
  workloadRef: string | null; configuration: Record<string, unknown> | null; pendingUpgradeInstanceId: string | null;
  // The real SME serviceId(s) this instance registered at bootstrap-complete
  // from its package's own smeDeclarations — null if the package declared
  // none, or before bootstrap-complete has run.
  smeServiceIds: string[] | null;
  // AUTONOMOUS's own pre-configured RAN node/cell/slice scope — opaque,
  // meaningless for ASSIST/SHADOW.
  regionScope: Record<string, unknown> | null;
  // Outcome of the most recent best-effort teardown (NFO terminate,
  // usage/stop) this row performed — on TERMINATE — or inherited through an
  // upgrade commit/rollback/timeout. Each step: DONE, SKIPPED…, or FAILED: …
  lastTeardown?: InstanceTeardown | null;
}
export interface InstanceTeardown {
  instanceId: string; reason: "TERMINATE" | "UPGRADE_COMMIT" | "UPGRADE_ROLLBACK" | "UPGRADE_TIMEOUT" | string;
  nfoTerminate: string; usageStop: string; at: string;
}
// OI-1-sa-rollback: rApp Management's version history — one entry per
// committed upgrade or rollback, with what the retired instance ran.
export interface InstanceVersion {
  versionId: string; kind: "UPGRADE" | "ROLLBACK"; instanceId: string; packageId: string;
  previousInstanceId: string; previousPackageId: string; previousConfiguration: Record<string, unknown> | null;
  rolledBackByVersionId: string | null; committedAt: string;
}
export interface InstanceVersions {
  instanceId: string; packageId: string; state: string; workloadRef: string | null;
  rollbackTarget: InstanceVersion | null; versions: InstanceVersion[];
}
export interface PerfReport { reportId: string; metrics: Record<string, unknown>; reportedAt: string }
export interface FaultReport { faultId: string; severity: string; description: string | null; reportedAt: string }

// ---- AI/ML Workflow
export interface Model {
  modelId: string; modelType: string; version: string;
  artifactLocation: string | null; description: string | null; author: string | null; owner: string | null;
  inputDataType: string | null; outputDataType: string | null; targetEnvironments: Record<string, unknown>[];
}
// aimgf's own model_lifecycle row (Wave 2) — MLMR's Model no longer
// carries state/clearedNodeGroups at all, see docs/ARCHITECTURE.md (MLMR).
export interface ModelLifecycle {
  modelId: string; modelLifecycleState: string; runtimeLifecycleState: string; trainingJobId: string | null;
  clearedNodeGroups: string[]; nfDeploymentDescriptorId: string | null; nfDeploymentId: string | null;
  trainingApproved: boolean; validationApproved: boolean;
}
export interface CertificationRecord {
  certificationRecordId: string; modelId: string; decision: string; decidedBy: string; rationale: string | null; decidedAt: string;
}
export interface TrainingJob {
  trainingJobId: string; modelId: string | null; modelCoordinationGroupId: string | null; producerId: string;
  status: string; runId: string | null; trainingDataset: string | null; validationDataset: string | null;
  modelMetrics: Record<string, unknown> | null;
  // HISTORY.md OI-6.2: MLTF's own real NFO-backed execution
  // runtime — set on request, cleared once the run completes.
  nfDeploymentId: string | null;
}
export interface InferenceJob {
  inferenceJobId: string; modelId: string; status: string; notificationDestination: string | null;
  // HISTORY.md OI-6.2: a reference to the model's own already-live
  // serving deployment (ModelLifecycle.nfDeploymentId) — not a new NFO
  // deployment of this job's own.
  nfDeploymentId: string | null;
}
export interface CoordinationGroup {
  groupId: string; groupType: string; memberModelIds: string[]; memberUseCases: string[];
  sharedFeaturePipelineRef: string | null; retrainPropagation: string;
}
export interface MlmfSubscription { subscriptionId: string; modelId: string; metricTypes: string[]; dmeTypeId: string; guardKpiFloor: Record<string, number> | null; notificationDestination: string | null }
export interface MlmfReport { reportId: string; subscriptionId: string; metrics: Record<string, unknown>; breachedFloor: boolean; reportedAt: string }

// ---- RAN NF OAM
export interface Alarm {
  alarmId: string; sourceAlarmId: string; managedElementRef: string; severity: string; ackState: string;
  raisedAt: string | null; correlationGroup: string | null; probableCause: string | null; specificProblem: string | null;
  rootCauseIndicator: boolean; correlatedNotifications: string[]; proposedRepairActions: string | null;
  alarmType: string | null; ackUserId: string | null; changedAt: string | null; clearedAt: string | null; clearUserId: string | null;
}
export interface PmSubscription { subscriptionId: string; managedElementRef: string; counterType: string; deliveryMethod: string; southboundEngine: string; granularityPeriod: number | null }
export interface FmSubscription { subscriptionId: string; managedElementRef: string; deliveryMethod: string; southboundEngine: string }
export interface O1Endpoint { endpointId: string; managedElementRef: string; adaptorUri: string; protocolSupport: string[]; registeredVia: string; healthStatus: string; lastHeartbeatAt: string | null }
export interface ConfigJobSummary { jobId: string; requestedBy: string; scope: string; status: string; msacRole: string | null }
export interface ConfigJob { jobId: string; status: string; subChanges: { managedElementRef: string; operation: string; status: string; rejectionReason: string | null }[] }
export interface SwmJob { jobId: string; managedElementRef: string; ruInstanceId: string | null; phase: string; status: string }

// ---- A1 Related / Intent Service
export interface A1Policy { policyId: string; policyTypeId: string; nearRtRicId: string; policyObject: Record<string, unknown>; enforcementStatus: string }
export interface Intent { intentId: string; intentAdminState: string; intentPriority: number; rmioId: string; intentMgmtPurpose: string | null; rmihId: string; userLabel: string | null; attributes: Record<string, unknown> }
/** Wave 6: a TS 28.312 IntentReport — every report kind lives under `attributes` (intentFulfilmentReport, intentConflictReports, ...). */
export interface IntentReport { reportId: string; intentId: string; attributes: Record<string, unknown> & { lastUpdatedTime: string } }
export interface RmihCapability { intentHandlingCapabilityId: string; supportedExpectationObjectType: string; supportedExpectationTargetInfoList: { supportedTargetName: string }[] }
export interface Rmih { rmihId: string; smeServiceId: string; notificationDestination: string; intentHandlingScope: string[] | null; attributes: { intentHandlingCapabilityList: RmihCapability[]; supportedNegotiationFunctionalities: string[] | null } }
// HISTORY.md OI-6.3 — rApp Autonomy Modes: a real record of each
// inference-driven dispatch decision, distinct from Intent itself since
// not every mode actually produces one (SHADOW never does; ASSIST
// doesn't until an operator resolves it).
export interface AutonomyDispatch {
  dispatchId: string; instanceId: string; modelId: string | null; autonomyMode: string;
  expectations: Record<string, unknown>[]; priority: number; rmihId: string;
  intentMgmtPurpose: string | null; intentHandlingScope: string | null;
  regionScope: Record<string, unknown> | null; status: string; intentId: string | null; createdAt: string;
  rejectedBy?: string | null; rejectionReason?: string | null;
}

// ---- NFO / FOCOM / SO / SA / Analytics / DME
export interface NfDeployment { nfDeploymentId: string; name: string; state: string; clusterId: string; nfDeploymentDescriptorId: string; workloadRef: string | null; requiredResourceTypeId: string | null }
export interface NfResource { resourceLinkId: string; resourceRef: string; vresourceType: string }
export interface ResourcePool { resourcePoolId: string; name: string; description: string | null; oCloudId: string }
export interface ResourceType { resourceTypeId: string; name: string; description: string | null; vendor: string | null; model: string | null; version: string | null; resourceKind: string | null; resourceClass: string | null }
export interface DeploymentManager { deploymentManagerId: string; name: string; description: string | null; oCloudId: string; serviceUri: string; capacity: unknown }
export interface OCloudResource { resourceId: string; resourceTypeId: string; resourcePoolId: string; parentId: string | null; description: string | null; globalAssetId: string | null }
export interface OCloudAlarm { alarmId: string; resourceRef: string; severity: string }
export interface OCloudMetric { resourceRef: string; metricName: string; value: number }
export interface Topology { entities: Record<string, { id: string; attributes: Record<string, unknown> }[]>[]; relationships: Record<string, { id: string; aSide: string; bSide: string }[]>[] }
export interface OrderStep { stepType: string; targetModule: string; status: string; error?: string; result?: Record<string, unknown>; [k: string]: unknown }
export interface ServiceOrder { orderId: string; scope: string; steps: OrderStep[]; homingDecision: Record<string, unknown> | null; rmihRegistration: string }
export interface Monitor { monitorId: string; targetOrderId: string | null; targetCoordinationGroupId: string | null; targetRappInstanceId: string | null; analyticsSubscriptionId: string | null; thresholds: Record<string, number> }
export interface RemedialAction { actionId: string; monitorId: string; actionType: string; autoExecuted: boolean; outcome: string | null }
export interface AnalyticsReport { reportId: string; analyticsType: string; output: Record<string, unknown> }
export interface AnalyticsProducer { producerId: string; analyticsType: string; mdaType: string | null; dmeInputTypes: string[]; outputSchema: Record<string, unknown> }
export interface AnalyticsSubscription { subscriptionId: string; analyticsType: string; requestedBy: string; notificationDestination: string | null; scope: Record<string, unknown> | null }
export interface DmeType { dmeTypeId: string; dmeTypeIdStruct: Record<string, string>; typeName: string; producerIds: string[]; typeStatus: string }
export interface DmeProducer { producerId: string; producerHealthCallbackUrl: string; jobCallbackUrl: string; supportedTypeIds: string[] }

// ---- BFF admin
export interface GuiUser { username: string; role: "viewer" | "operator" | "admin"; active: boolean; createdAt: string }
export interface AuditEntry { id: number; at: string; username: string | null; role: string | null; action: string; method: string | null; path: string | null; statusCode: number | null; detail: string | null }

// ---- GUI pass 2: DME, A1 EI/services, SME registries, onboarding/NFO detail
export interface DataJob { dataJobId: string; dataDeliveryMode: string; dmeTypeId: string; productionJobDefinition: Record<string, unknown>; dataDeliveryMethod: string; deliveryDetails: Record<string, unknown>; consumerId: string; status: string }
export interface DataOffer { offerId: string; dmeTypeId: string; dataDeliveryMethodsOffered: string[]; committedMethod: string | null; dataAvailabilityNotificationUri: string | null; dataOfferTerminationNotificationUri: string }
export interface DmeTypeSubscription { subscriptionId: string; notificationDestination: string; owner: string }
export interface EiType { eiTypeId: string; registeredBy: string; eiSourceDmeTypeId: string }
export interface A1Service { serviceId: string; callbackUrl: string | null; keepAliveIntervalSeconds: number; timeSinceLastActivitySeconds?: number; [k: string]: unknown }
export interface PolicyStatusSubscription { subscriptionId: string; notificationDestination: string; subscriptionScope: string | null; policyIdList: string[] | null; policyTypeIdList: string[] | null; nearRtRicIdList: string[] | null }
export interface SmeProvider { apfId: string; providerDomainInfo: string | null; serviceCount: number }
export interface SmeService { serviceId: string; serviceName: string; producerId: string; endpoint: string; version: string; fullApiVersions: string[]; serviceCapabilities: Record<string, unknown>; aefProfiles: Record<string, unknown>[] }
export interface SmeInvoker { apiInvokerId: string; apiInvokerPublicKey: string; trusted: boolean }
export interface TrustedInvoker { apiInvokerId: string; notificationDestination: string; requestTestNotification: boolean; securityInfo: Record<string, unknown>[] }
export interface CapifEventSubscription { subscriptionId: string; subscriberId: string; eventTypes: string[]; callbackUri: string; apiIds: string[] | null }
export interface PackageUsage { registrationId: string; consumerId: string; stoppedAt: string | null; active: boolean }
export interface PackageArtifact { artifactId: string; path: string; accessUrl: string }
export interface NfDescriptor { nfDeploymentDescriptorId: string; packageId: string; name: string; requiredResourceTypeId: string | null; workloadTemplate: Record<string, unknown> }
export interface LcmOperation { operationId: string; operationType: string; status: string }
export interface InventorySubscription { subscriptionId: string; callback: string; consumerSubscriptionId: string | null; resourceTypeId: string | null }
export interface FeatureGroup { featureGroupId: string; featureGroupName: string; featureList: string; datalakeSource: string; host: string; port: string; bucket: string; dbOrg: string; measurement: string; enableDme: boolean; measuredObjClass: string | null; sourceName: string | null }

// ---- Wave 10.1: EnergySaving reference rApp (samples/energy-saving-rapp)
export interface EsInstance {
  instanceId: string; packageId: string | null; managedElementRef: string; cells: string[]; actuator: string;
  autonomyMode: string; rmihId: string; datasets: Record<string, { dataset: string; dmeTypeId: string; dataJobId: string; sourceDomain: string | null }>;
  modelId: string | null; modelVersion: string | null; artifactVersion: number | null; model: Record<string, unknown> | null;
  lifecycleJobs: Record<string, string>;
}
export interface EsDecision {
  decisionId: string; executionId: string; cellId: string; observedAt: string | null; prb: number | null;
  prediction: { model?: { futurePrb: number; recommendedState: string; confidence: number } | null; mdafFuturePrb?: number | null; lowForMinutes?: number } | null;
  safety: { passed: boolean; blocks: { guard: string; level: string; detail?: unknown }[] } | null;
  decision: string; reason: string; outcome: string;
  intent: { dispatchId: string; autonomyMode: string; status: string; intentId: string | null } | null;
  action: { path: string; actionId: string; status: string; forwardedJobId?: string | null } | null;
  verification: { result: string; expected: string; observed: Record<string, string | null> } | null;
  rollback: { trigger: string; performed: boolean; result: string } | null;
  finalState: { state: string; o1: string | null } | null; createdAt: string;
}
export interface EsCell {
  cellId: string; state: string; o1Value: string | null; lastUnlockedAt: string | null; overrideBy: string | null;
  pendingDispatchId: string | null; prbTrend: { t: string; v: number }[]; latestDecision: EsDecision | null;
}
export interface EsDashboard { instance: EsInstance; cells: EsCell[] }

// ---- Wave 10.2: Mobility Optimization reference rApp (samples/mobility-optimization-rapp)
export interface MroRelationRef { relation: string; source: string; target: string }
export interface MroInstance {
  instanceId: string; packageId: string | null; managedElementRef: string; relations: MroRelationRef[]; baselineCio: number;
  dmroBounds: Record<string, unknown> | null; autonomyMode: string; rmihId: string; energySavingInstanceId: string | null;
  datasets: Record<string, { dataset: string; dmeTypeId: string; dataJobId: string; sourceDomain: string | null }>;
  modelId: string | null; modelVersion: string | null; artifactVersion: number | null; model: Record<string, unknown> | null;
  lifecycleJobs: Record<string, string>;
}
export interface MroDecision {
  decisionId: string; executionId: string; relation: string; observedAt: string | null; rate: number | null; attempts: number | null;
  prediction: { model?: { rate: number; futureRate: number; cause: string | null; recommendation: string; confidence: number } | null } | null;
  safety: { passed: boolean; blocks: { guard: string; level: string; detail?: unknown }[] } | null;
  decision: string; reason: string; fromCio: number | null; toCio: number | null;
  kpi: { preRate: number; postRate: number; windows: number; verdict: string } | null;
  intent: { dispatchId: string; autonomyMode: string; status: string; intentId: string | null } | null;
  action: { path: string; actionId: string; status: string } | null;
  verification: { result: string } | null;
  rollback: { trigger: string; performed: boolean; result: string } | null;
  outcome: string; finalState: { state: string; cio: number | null } | null; createdAt: string;
}
export interface MroRelation extends MroRelationRef {
  state: string; cio: number | null; lastChange: Record<string, unknown> | null; pendingDispatchId: string | null;
  rateTrend: { t: string; v: number }[]; latestDecision: MroDecision | null;
}
export interface MroDashboard { instance: MroInstance; relations: MroRelation[] }

// ---- Wave 10.3: Coverage Optimization reference rApp (samples/coverage-optimization-rapp)
export type CcoShares = { WEAK_COVERAGE: number; OVERSHOOT: number; PILOT_POLLUTION: number };
export interface CcoSetting { digitalTilt: number | null; configuredMaxTxPower: number | null }
export interface CcoInstance {
  instanceId: string; packageId: string | null; managedElementRef: string; cells: string[]; baselineTilt: number; baselinePower: number;
  autonomyMode: string; rmihId: string; energySavingInstanceId: string | null; mobilityInstanceId: string | null;
  observing: { changeSetId: string; at: string; cells: Record<string, { move: string }>; preObjective: number; predictedObjective: number } | null;
  pendingDispatchId: string | null;
  datasets: Record<string, { dataset: string; dmeTypeId: string; dataJobId: string; sourceDomain: string | null }>;
  modelId: string | null; modelVersion: string | null; artifactVersion: number | null; model: Record<string, unknown> | null;
  lifecycleJobs: Record<string, string>;
}
export interface CcoPlan {
  moves: Record<string, string>; drivers: Record<string, string>; objectiveBefore: number; objectiveAfter: number; gain: number;
  inferenceJobId: string; aimlInferenceReportId: string | null;
}
export interface CcoDecision {
  decisionId: string; executionId: string; cellId: string; observedAt: string | null; reports: number | null; shares: CcoShares | null;
  prediction: { plan: CcoPlan; predictedShares: CcoShares | null; problem: string | null; confidence: number } | null;
  safety: { passed: boolean; blocks: { guard: string; level: string; detail?: unknown }[]; allowedMoves?: string[] } | null;
  decision: string; reason: string; fromSetting: CcoSetting | null; toSetting: CcoSetting | null;
  kpi: { preObjective: number; postObjective: number; verdict: string } | null;
  intent: { dispatchId: string; autonomyMode: string; status: string; intentId: string | null } | null;
  action: { path: string; actionId: string; status: string } | null;
  verification: { result: string } | null;
  rollback: { trigger: string; performed: boolean; result: string } | null;
  outcome: string; finalState: ({ state: string } & CcoSetting) | null; createdAt: string;
}
export interface CcoCell extends CcoSetting {
  cellId: string; state: string; lastChangedAt: string | null;
  shareTrend: ({ t: string } & CcoShares)[]; excessTrend: { t: string; v: number }[]; latestDecision: CcoDecision | null;
}
export interface CcoDashboard { instance: CcoInstance; cells: CcoCell[] }

// ---- Wave 10.4: Traffic Steering reference rApp (samples/traffic-steering-rapp)
export interface TsSteering { cio: Record<string, number>; prio: Record<string, number> }
export interface TsInstance {
  instanceId: string; packageId: string | null; managedElementRef: string; cells: { cellId: string; layer: string }[];
  baselineCio: number; baselinePriority: number; autonomyMode: string; rmihId: string;
  energySavingInstanceId: string | null; mobilityInstanceId: string | null; coverageInstanceId: string | null;
  steeringLog: { source: string; targets: string[]; at: string }[]; pendingDispatchId: string | null;
  datasets: Record<string, { dataset: string; dmeTypeId: string; dataJobId: string; sourceDomain: string | null }>;
  modelId: string | null; modelVersion: string | null; artifactVersion: number | null; model: Record<string, unknown> | null;
  lifecycleJobs: Record<string, string>;
}
export interface TsPlan {
  decision: string; reason: string; move?: { knob: string; ref: string; target?: string; layer?: string; targets?: string[]; from: number; to: number };
  movedScore?: number; targetForecastAfter?: number; sourceForecastAfter?: number; rejected?: Record<string, unknown>[];
}
export interface TsDecision {
  decisionId: string; executionId: string; cellId: string; observedAt: string | null; score: number | null; forecast: number | null;
  prediction: { model: { score: number; forecast: number; band: string; confidence: number }; plan: TsPlan } | null;
  safety: { passed: boolean; blocks: { guard: string; level: string; detail?: unknown }[]; excluded?: Record<string, string>[] } | null;
  decision: string; reason: string; knob: string | null; managedRef: string | null; targets: string[] | null;
  fromValue: number | null; toValue: number | null;
  kpi: { verdict: string; causes: string[]; postSource: number | null; preForecast: number } | null;
  intent: { dispatchId: string; autonomyMode: string; status: string; intentId: string | null } | null;
  action: { path: string; actionId: string; status: string } | null;
  verification: { result: string } | null;
  rollback: { trigger: string; performed: boolean; result: string } | null;
  outcome: string; finalState: { state: string; steering: TsSteering } | null; createdAt: string;
}
export interface TsCell {
  cellId: string; layer: string; state: string; steering: TsSteering; lastChangedAt: string | null;
  scoreTrend: { t: string; v: number }[]; latestDecision: TsDecision | null;
}
export interface TsDashboard { instance: TsInstance; cells: TsCell[] }
