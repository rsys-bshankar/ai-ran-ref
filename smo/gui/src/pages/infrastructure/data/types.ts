/** View-model types of the Infrastructure page: the topology tree built from FOCOM's TEIV export (`data/topology.ts`) and the O2-IMS inventory
 * objects FOCOM lists (`/focom/locations` … `/focom/performance-jobs`), which `api/types.ts` does not describe. Field names follow the FOCOM
 * wire shapes (focom/app/common.py, provisioning.py, fcaps.py); every field a row may lack is optional. */

/** A node's health as the page shows it: from open O-Cloud alarms (resources), from the deployment state (workloads), rolled up to the worst
 * child (pools, O-Clouds); `unknown` when no data the backend serves says anything (deployment managers, an empty pool). */
export type Health = "ok" | "warn" | "bad" | "unknown";

/** The kinds of node in the topology tree. */
export type NodeKind = "ocloud" | "dm" | "pool" | "resource" | "workload";

/** One node of the topology tree. `id` is unique across kinds (`<kind>:<backend id>`); `ref` is the backend id. */
export interface TopoNode {
  id: string;
  kind: NodeKind;
  ref: string;
  name: string;
  health: Health;
  /** Why the node has that health, in a short sentence (the inspector shows it). */
  healthWhy: string;
  parent: string | null;
  children: string[];
  attributes: Record<string, unknown>;
  /** Workloads: the NF deployment state. */
  state?: string;
}

/** The built tree: every node by id, the roots (O-Clouds), and what was cut while building it. */
export interface TopoTree {
  nodes: Map<string, TopoNode>;
  roots: string[];
  /** Notes on bounded inputs (a workload or alarm list cut at its limit), shown under the graph. */
  notes: string[];
}

/** O2-IMS Location (`/focom/locations`). */
export interface OLocation { globalLocationId: string; name: string; description?: string; oCloudId?: string; oCloudSiteIds?: string[]; coordinate?: string | null; address?: string | null }
/** O2-IMS O-Cloud site (`/focom/o-cloud-sites`); its pools come inline. */
export interface OSite { oCloudSiteId: string; locationId: string; name: string; description?: string; oCloudId?: string; resourcePools?: { resourcePoolId: string; name: string }[] }
/** O2-IMS NodeCluster (`/focom/node-clusters`). */
export interface ONodeCluster { nodeClusterId: string; name: string; description?: string; nodeClusterTypeId?: string; clusterDistributionDescription?: string; clusterResourceIds?: string[]; artifactResourceId?: string }
/** O2-IMS ClusterResource (`/focom/cluster-resources`). */
export interface OClusterResource { clusterResourceId: string; name: string; description?: string; clusterResourceTypeId?: string; resourceId?: string; memberOf?: string[] }
/** O2-IMS InfrastructureResource (`/focom/infrastructure-resources`). */
export interface OInfraResource { infrastructureResourceId: string; name: string; description?: string; infrastructureResourceTypeId?: string; inventoryResourceIds?: string[]; artifactResourceId?: string }
/** O2-IMS ProvisioningRequest (`/focom/provisioning-requests`). */
export interface OProvisioningRequest {
  provisioningRequestId: string; name: string; description?: string; templateName?: string; templateVersion?: string;
  provisionedResourceSet?: { nodeClusterId?: string; infrastructureResourceIds?: string[] };
  status?: { updateTime?: string; message?: string; provisioningPhase?: string };
}
/** O2-IMS PerformanceMeasurementJob (`/focom/performance-jobs`). */
export interface OPerformanceJob {
  performanceMeasurementJobId: string; consumerPerformanceJobId?: string | null; state?: string; status?: string | null; collectionInterval?: number;
  measuredResources?: unknown[]; collectedMeasurements?: unknown[];
}
/** An O-Cloud alarm as `/focom/alarms` returns it (only the fields the topology health reads). */
export interface OAlarm { alarmId: string; resourceRef: string; severity: string; alarmClearedTime?: string | null }
