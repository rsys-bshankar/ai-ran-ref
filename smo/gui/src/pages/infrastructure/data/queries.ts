/** Every API path, query parameter and polling choice of the Infrastructure page (STRUCTURE.md rule 4): sections call these hooks or pass these
 * paths to `kit/ServerTable`, never `useSmo` with a raw path. Each tab mounts only its own sections, so only the visible tab's queries run
 * (SCALE.md §4: Infrastructure used to make 18 calls on first load; now about 2–3 per tab, and inventory polls every 60 s). */
import type { Query } from "../../../api/client";
import { POLL, useSmo, useSmoPage } from "../../../api/hooks";
import type { KpiDef, LcmOperation, Model, NfDeployment, NfDescriptor, NfResource, O1Endpoint, ServiceOrder, Topology } from "../../../api/types";
import { useSummary } from "../../../data/summary";
import type { OAlarm } from "./types";

/** How many workloads and alarms the topology reads at most (one bounded page each, the module's MAX_LIMIT). */
export const TOPOLOGY_LIMIT = 500;

/** How often the inventory tables (FOCOM) refresh: inventory changes slowly (SCALE.md: staleness ≤ 60 s). */
export const INVENTORY_POLL = POLL.inventory;

/** The list routes the page's server tables read. */
export const PATHS = {
  deployments: "/nfo/deployments",
  descriptors: "/nfo/descriptors",
  endpoints: "/ran-nf-oam/o1-adaptor-endpoints",
  configJobs: "/ran-nf-oam/config-jobs",
  swmJobs: "/ran-nf-oam/software-management-jobs",
  orders: "/so-smos/orders",
  inventorySubscriptions: "/focom/inventory/subscriptions",
  poolResources: (poolId: string) => `/focom/resource-pools/${poolId}/resources`,
} as const;

/** The NF deployment states the NFO list filters on (`?state=`). */
export const DEPLOYMENT_STATES = ["INITIAL", "INSTANTIATING", "RUNNING", "UPDATING", "TERMINATING", "ABNORMAL", "DELETING"] as const;

/** The page's summary counts (`deployments.<STATE>`, `deployments.total`, `elements.total`) from the BFF. */
export function useInfraSummary() {
  return useSummary("infrastructure", { refetchInterval: POLL.inventory });
}

/** FOCOM's TEIV-shaped topology export (one unpaginated call). */
export function useTopology() {
  return useSmo<Topology>("/focom/topology", undefined, { refetchInterval: POLL.inventory });
}

/** The workloads the topology places on each O-Cloud: one bounded page of NF deployments, with its envelope so a cut can be named. */
export function useTopologyWorkloads() {
  return useSmoPage<NfDeployment>(PATHS.deployments, { limit: TOPOLOGY_LIMIT, offset: 0 }, { refetchInterval: POLL.inventory });
}

/** The O-Cloud alarms the topology reads a resource's health from: one bounded page. */
export function useTopologyAlarms() {
  return useSmoPage<OAlarm>("/focom/alarms", { limit: TOPOLOGY_LIMIT, offset: 0 }, { refetchInterval: POLL.inventory });
}

/** One NF deployment's linked O-Cloud resources and LCM operations (the deployment drawer). */
export function useDeploymentDetail(id: string) {
  return {
    resources: useSmo<NfResource[]>(`/nfo/deployments/${id}/resources`),
    operations: useSmo<LcmOperation[]>(`/nfo/deployments/${id}/operations`),
  };
}

/** O1 endpoints to pick from in a form (the config-write modal, the software-update form): one bounded page, read only while the form is open. */
export function useEndpointChoices(enabled: boolean) {
  return useSmoPage<O1Endpoint>(PATHS.endpoints, { limit: TOPOLOGY_LIMIT, offset: 0 }, { enabled });
}

/** The KPI definitions a config write's KPI guard can watch. */
export function useKpiDefinitions() {
  return useSmo<KpiDef[]>("/ran-nf-oam/kpi-definitions");
}

/** One page of service orders. */
export function useOrdersPage(q: Query) {
  return useSmoPage<ServiceOrder>(PATHS.orders, q, { refetchInterval: POLL.lists });
}

/** What the order form prefills TRAINING / DEPLOY steps from (newest model, a not-yet-deployed descriptor); read only while the form is open. */
export function useOrderPrefill(enabled: boolean) {
  return {
    models: useSmo<Model[]>("/mlmr/models", undefined, { enabled }),
    descriptors: useSmo<NfDescriptor[]>(PATHS.descriptors, undefined, { enabled }),
    deployments: useSmo<NfDeployment[]>(PATHS.deployments, undefined, { enabled }),
  };
}

/** One level of the O-Cloud inventory picker: its label, list route, row id and what the level holds. */
export interface InventoryLevel { id: string; label: string; path: string; group: "place" | "work" | "ims" }

/** The O-Cloud inventory levels, from where it is to what runs there (BRIEF §4e feature 6), then the O2-IMS pools, managers and types. */
export const INVENTORY_LEVELS: InventoryLevel[] = [
  { id: "locations", label: "Locations", path: "/focom/locations", group: "place" },
  { id: "sites", label: "Sites", path: "/focom/o-cloud-sites", group: "place" },
  { id: "clusters", label: "Node clusters", path: "/focom/node-clusters", group: "place" },
  { id: "cluster-resources", label: "Cluster resources", path: "/focom/cluster-resources", group: "place" },
  { id: "infra-resources", label: "Infrastructure resources", path: "/focom/infrastructure-resources", group: "place" },
  { id: "provisioning", label: "Provisioning requests", path: "/focom/provisioning-requests", group: "work" },
  { id: "perf-jobs", label: "Performance jobs", path: "/focom/performance-jobs", group: "work" },
  { id: "pools", label: "Resource pools", path: "/focom/resource-pools", group: "ims" },
  { id: "dms", label: "Deployment managers", path: "/focom/deployment-managers", group: "ims" },
  { id: "types", label: "Resource types", path: "/focom/resource-types", group: "ims" },
];
