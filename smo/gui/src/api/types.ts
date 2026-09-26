// Response shapes, taken from each module's own view functions
// (smo/<module>/app/main.py) — docs/openapi/<module>.json is the contract.

export interface Me { username: string; role: "viewer" | "operator" | "admin"; csrfToken?: string }

export interface ModuleStatus { module: string; healthy: boolean; latencyMs: number; statusCode: number | null; error: string | null }
export interface ModulesStatus { checkedAt: string; modules: ModuleStatus[] }

// ---- Onboarding / rApp Management
export interface Package {
  packageId: string; name: string; version: string; vendor: string | null; applicationType: string;
  state: string; toscaEntryDefinitions: string | null; signatureVerified: boolean; nfDeploymentDescriptorId: string | null;
}
export interface InstanceSummary { instanceId: string; packageId: string; state: string }
export interface Instance extends InstanceSummary {
  workloadRef: string | null; configuration: Record<string, unknown> | null; pendingUpgradeInstanceId: string | null;
}
export interface PerfReport { reportId: string; metrics: Record<string, unknown>; reportedAt: string }
export interface FaultReport { faultId: string; severity: string; description: string | null; reportedAt: string }

// ---- AI/ML Workflow
export interface Model {
  modelId: string; modelType: string; version: string; state: string; clearedNodeGroups: string[];
  artifactLocation: string | null; description: string | null; author: string | null; owner: string | null;
  inputDataType: string | null; outputDataType: string | null; targetEnvironments: Record<string, unknown>[];
}
export interface TrainingJob {
  trainingJobId: string; modelId: string | null; modelCoordinationGroupId: string | null; producerId: string;
  status: string; runId: string | null; trainingDataset: string | null; validationDataset: string | null;
  modelMetrics: Record<string, unknown> | null;
}
export interface InferenceJob { inferenceJobId: string; modelId: string; status: string; notificationDestination: string | null }
export interface CoordinationGroup {
  groupId: string; groupType: string; memberModelIds: string[]; memberUseCases: string[];
  sharedFeaturePipelineRef: string | null; retrainPropagation: string;
}
export interface MlmfSubscription { subscriptionId: string; modelId: string; metricTypes: string[]; dmeTypeId: string; guardKpiFloor: Record<string, number> | null }
export interface MlmfReport { reportId: string; subscriptionId: string; metrics: Record<string, unknown>; breachedFloor: boolean; reportedAt: string }

// ---- RAN NF OAM
export interface Alarm {
  alarmId: string; sourceAlarmId: string; managedElementRef: string; severity: string; ackState: string;
  raisedAt: string | null; correlationGroup: string | null; probableCause: string | null; specificProblem: string | null;
  rootCauseIndicator: boolean; correlatedNotifications: string[]; proposedRepairActions: string | null;
  alarmType: string | null; ackUserId: string | null; changedAt: string | null; clearedAt: string | null; clearUserId: string | null;
}
export interface PmSubscription { subscriptionId: string; managedElementRef: string; counterType: string; deliveryMethod: string; southboundEngine: string; granularityPeriod: number | null }
export interface O1Endpoint { endpointId: string; managedElementRef: string; adaptorUri: string; protocolSupport: string[]; registeredVia: string; healthStatus: string; lastHeartbeatAt: string | null }
export interface ConfigJobSummary { jobId: string; requestedBy: string; scope: string; status: string; msacRole: string | null }
export interface ConfigJob { jobId: string; status: string; subChanges: { managedElementRef: string; operation: string; status: string; rejectionReason: string | null }[] }
export interface SwmJob { jobId: string; managedElementRef: string; ruInstanceId: string | null; phase: string; status: string }

// ---- A1 Related / Policy Mgmt
export interface A1Policy { policyId: string; policyTypeId: string; nearRtRicId: string; policyObject: Record<string, unknown>; enforcementStatus: string }
export interface Intent { intentId: string; intentAdminState: string; intentPriority: number; rmioId: string; intentMgmtPurpose: string | null }
export interface IntentReport { reportId: string; intentId: string; fulfilmentReport: Record<string, unknown> | null; conflictReports: unknown[] | null; lastUpdatedTime: string }
export interface Rmih { rmihId: string; smeServiceId: string; capabilities: Record<string, unknown>[]; notificationCallbackUri: string; intentHandlingScope: string[] | null }

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
export interface Monitor { monitorId: string; targetOrderId: string | null; targetCoordinationGroupId: string | null; analyticsSubscriptionId: string | null; thresholds: Record<string, number> }
export interface RemedialAction { actionId: string; monitorId: string; actionType: string; autoExecuted: boolean; outcome: string | null }
export interface AnalyticsReport { reportId: string; analyticsType: string; output: Record<string, unknown> }
export interface AnalyticsProducer { producerId: string; analyticsType: string; dmeInputTypes: string[]; outputSchema: Record<string, unknown> }
export interface AnalyticsSubscription { subscriptionId: string; analyticsType: string; requestedBy: string; notificationDestination: string | null; scope: Record<string, unknown> | null }
export interface DmeType { dmeTypeId: string; dmeTypeIdStruct: Record<string, string>; typeName: string; producerId: string; typeStatus: string }

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
