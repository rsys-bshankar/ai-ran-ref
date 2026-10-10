/** The table columns and row id of each O-Cloud inventory level (`data/queries.ts` INVENTORY_LEVELS), from the FOCOM wire shapes. Kept apart
 * from `OCloudInventory.tsx` so a new level is one entry here and one in the levels table. */
import type { ReactNode } from "react";

import type { DeploymentManager, ResourcePool, ResourceType } from "../../../api/types";
import { Id, StateBadge, type Column } from "../../../components/ui";
import type { OClusterResource, OInfraResource, OLocation, ONodeCluster, OPerformanceJob, OProvisioningRequest, OSite } from "../data/types";

/** A level's columns and row key, typed loosely because the picker switches between them. */
export interface LevelSpec { rowKey: (r: never) => string; columns: Column<never>[] }

/** Builds a {@link LevelSpec} with the row type checked. */
function spec<T>(rowKey: (r: T) => string, columns: Column<T>[]): LevelSpec {
  return { rowKey: rowKey as (r: never) => string, columns: columns as Column<never>[] };
}

/** "—" for an empty value. */
const dash = (v: ReactNode) => (v === null || v === undefined || v === "" ? <span className="muted">—</span> : v);
/** The length of a list attribute, or "—". */
const len = (v: unknown[] | undefined) => (v ? v.length : <span className="muted">—</span>);

/** Columns per level id. */
export const LEVEL_SPECS: Record<string, LevelSpec> = {
  locations: spec<OLocation>((r) => r.globalLocationId, [
    { header: "Location", render: (r) => <><strong>{r.name}</strong><div className="muted small">{r.description}</div></> },
    { header: "ID", render: (r) => <Id value={r.globalLocationId} /> },
    { header: "Address / coordinate", render: (r) => dash([r.address, r.coordinate].filter(Boolean).join(" · ")) },
    { header: "Sites", render: (r) => len(r.oCloudSiteIds) },
  ]),
  sites: spec<OSite>((r) => r.oCloudSiteId, [
    { header: "Site", render: (r) => <><strong>{r.name}</strong><div className="muted small">{r.description}</div></> },
    { header: "ID", render: (r) => <Id value={r.oCloudSiteId} /> },
    { header: "Location", render: (r) => <Id value={r.locationId} /> },
    { header: "O-Cloud", render: (r) => dash(r.oCloudId) },
    { header: "Pools", render: (r) => len(r.resourcePools) },
  ]),
  clusters: spec<ONodeCluster>((r) => r.nodeClusterId, [
    { header: "Node cluster", render: (r) => <><strong>{r.name}</strong><div className="muted small">{r.description}</div></> },
    { header: "ID", render: (r) => <Id value={r.nodeClusterId} /> },
    { header: "Type", render: (r) => <Id value={r.nodeClusterTypeId} /> },
    { header: "Distribution", render: (r) => <span className="small">{dash(r.clusterDistributionDescription)}</span> },
    { header: "Cluster resources", render: (r) => len(r.clusterResourceIds) },
  ]),
  "cluster-resources": spec<OClusterResource>((r) => r.clusterResourceId, [
    { header: "Cluster resource", render: (r) => <><strong>{r.name}</strong><div className="muted small">{r.description}</div></> },
    { header: "ID", render: (r) => <Id value={r.clusterResourceId} /> },
    { header: "Type", render: (r) => <Id value={r.clusterResourceTypeId} /> },
    { header: "Resource", render: (r) => <Id value={r.resourceId} /> },
    { header: "Groups", render: (r) => len(r.memberOf) },
  ]),
  "infra-resources": spec<OInfraResource>((r) => r.infrastructureResourceId, [
    { header: "Infrastructure resource", render: (r) => <><strong>{r.name}</strong><div className="muted small">{r.description}</div></> },
    { header: "ID", render: (r) => <Id value={r.infrastructureResourceId} /> },
    { header: "Type", render: (r) => <Id value={r.infrastructureResourceTypeId} /> },
    { header: "Inventory resources", render: (r) => len(r.inventoryResourceIds) },
  ]),
  provisioning: spec<OProvisioningRequest>((r) => r.provisioningRequestId, [
    { header: "Request", render: (r) => <><strong>{r.name}</strong><div className="muted small">{r.description}</div></> },
    { header: "ID", render: (r) => <Id value={r.provisioningRequestId} /> },
    { header: "Template", render: (r) => <code className="small">{r.templateName}@{r.templateVersion}</code> },
    { header: "Phase", render: (r) => <StateBadge state={r.status?.provisioningPhase} /> },
    { header: "Node cluster", render: (r) => <Id value={r.provisionedResourceSet?.nodeClusterId} /> },
    { header: "Message", render: (r) => <span className="small">{dash(r.status?.message)}</span> },
  ]),
  "perf-jobs": spec<OPerformanceJob>((r) => r.performanceMeasurementJobId, [
    { header: "Job", render: (r) => <Id value={r.performanceMeasurementJobId} /> },
    { header: "Consumer job", render: (r) => dash(r.consumerPerformanceJobId) },
    { header: "State", render: (r) => <StateBadge state={r.state} /> },
    { header: "Interval, s", render: (r) => dash(r.collectionInterval) },
    { header: "Measured resources", render: (r) => len(r.measuredResources) },
  ]),
  pools: spec<ResourcePool>((r) => r.resourcePoolId, [
    { header: "Pool", render: (p) => <><strong>{p.name}</strong><div className="muted small">{p.description}</div></> },
    { header: "O-Cloud", render: (p) => p.oCloudId },
  ]),
  dms: spec<DeploymentManager>((r) => r.deploymentManagerId, [
    { header: "Name", render: (d) => <strong>{d.name}</strong> }, { header: "Service URI", render: (d) => <code className="small">{d.serviceUri}</code> },
    { header: "O-Cloud", render: (d) => d.oCloudId },
  ]),
  types: spec<ResourceType>((r) => r.resourceTypeId, [
    { header: "Type", render: (t) => <><strong>{t.name}</strong> <code className="small">{t.resourceTypeId}</code></> },
    { header: "Vendor / model", render: (t) => [t.vendor, t.model, t.version].filter(Boolean).join(" ") || "—" },
    { header: "Kind / class", render: (t) => [t.resourceKind, t.resourceClass].filter(Boolean).join(" / ") || "—" },
  ]),
};
