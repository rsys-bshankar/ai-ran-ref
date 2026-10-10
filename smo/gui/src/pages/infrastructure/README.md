# Infrastructure
Route: /infrastructure    Owner: SMO GUI    Design: `docs/redesign/designs/Infrastructure.dc.html`, SCALE.md "Infrastructure · at scale"

Tabs (URL hash): `#topology` (default), `#nfo`, `#ocloud`, `#o1`, `#orders`. The old ids are unchanged, so links from the Dashboard and Flows
(`/infrastructure#nfo`, `#o1`, `#orders`) land on the same tab. Each tab mounts only its own sections; the summary call is shared by every tab.

## Sections
| id | file | what it shows | API (via data/queries.ts) | refresh | budget |
| --- | --- | --- | --- | --- | --- |
| infrastructure.topology | sections/TopologyLevels.tsx | TEIV topology collapsed by level (O-Cloud → managers, pools, workloads → resources → child resources), health colours + words, breadcrumb, jump-to-node, ≤ 200 tiles + "+N more" | `/focom/topology`, `/nfo/deployments?limit=500`, `/focom/alarms?limit=500`; tree in `data/topology.ts` | 60 s | 3 calls |
| infrastructure.inspector | sections/NodeInspector.tsx | selected node: kind, health and why, TEIV attributes, children, what runs on it, utilisation "—", deprovision (resources) | none (same tree) | — | 0 |
| infrastructure.deployment-tiles | sections/NfDeployments.tsx | deployment counts by state; a tile filters the table | `/api/summary/infrastructure` | 60 s | 1 call (shared) |
| infrastructure.deployments | sections/NfDeployments.tsx, DeploymentDrawer.tsx | server table with `?state=` filter, heal / scale / terminate / async terminate, drawer with DMS report, LCM operations, resources | `/nfo/deployments`, `/nfo/deployments/{id}/resources`, `/operations` | 15 s | 1 call/page (+2 on open) |
| infrastructure.descriptors | sections/NfDeployments.tsx | NF deployment descriptors, server table | `/nfo/descriptors` | 15 s | 1 call/page |
| infrastructure.inventory | sections/OCloudInventory.tsx, InventoryColumns.tsx | level picker: locations → sites → node clusters → cluster resources → infrastructure resources; provisioning requests, performance jobs; pools (→ pool resources, deprovision, provision), deployment managers, resource types. One level listed at a time | `/focom/locations`, `/o-cloud-sites`, `/node-clusters`, `/cluster-resources`, `/infrastructure-resources`, `/provisioning-requests`, `/performance-jobs`, `/resource-pools`, `/resource-pools/{id}/resources`, `/deployment-managers`, `/resource-types` | 60 s | 1 call/page |
| infrastructure.inventory-subscriptions | sections/InventorySubscriptions.tsx | inventory-change subscriptions, subscribe / unsubscribe | `/focom/inventory/subscriptions` | 60 s | 1 call/page |
| infrastructure.o1-endpoints | sections/O1Endpoints.tsx | O1 endpoints with `?health_status=` filter, region / tenant, heartbeat, health discovery, register | `/ran-nf-oam/o1-adaptor-endpoints` | 15 s | 1 call/page |
| infrastructure.config-jobs | sections/O1Jobs.tsx, ConfigWrite.tsx | CM write jobs with `?status=` filter, job drawer, "New config write" (staged rollout, KPI guard) | `/ran-nf-oam/config-jobs`; the dialog reads `/o1-adaptor-endpoints?limit=500` and `/kpi-definitions` while open | 15 s | 1 call/page |
| infrastructure.swm-jobs | sections/O1Jobs.tsx | software management jobs, start update (element list read once opened), advance / fail phase | `/ran-nf-oam/software-management-jobs` | 15 s | 1 call/page |
| infrastructure.submit-order | sections/SubmitOrder.tsx | "New service order": scope + JSON steps, Add-step templates prefilled with the newest model / a free descriptor | `/mlmr/models`, `/nfo/descriptors`, `/nfo/deployments` only while the form is open; `POST /so-smos/orders` | — | 0 until opened |
| infrastructure.orders | sections/ServiceOrders.tsx | order cards with the steps as a stepper (done / fail / not reached), overall state, cancel pending, details drawer, pager | `/so-smos/orders` | 15 s | 1 call/page |

First-load calls per tab: Topology 4 (topology, workloads, alarms, summary), NF deployments 3, O-Cloud inventory 3, O1 4, Service orders 2.

## Known limits
- ⚠ **Node utilisation** (GPU / CPU / memory) is not served by any module: the inspector shows "—" with a gap note and "colour by GPU / CPU"
  is not offered (BRIEF §5). PR-GUI-9.8 looked at FOCOM and left it out on purpose: FOCOM collects no CPU or memory measurement and no
  dictionary names one (focom/README.md, "No node utilisation"), so a utilisation route would invent the numbers.
- **Health** of a resource comes from open O-Cloud alarms naming it (`resourceRef`); FOCOM's `/resources/{id}/status` answers a constant
  "healthy", so it is not used. Deployment managers have no health data ("No health data"). Pools and O-Clouds take the worst of what is under them.
- TEIV (`/focom/topology`) has **no deployment-manager → pool relationship**, and NFO places a workload on the O-Cloud (`clusterId` = O-Cloud id),
  not on a resource. So managers, pools and workloads are siblings under their O-Cloud; "what runs on" a pool or resource is not known and the
  inspector says so. Locations, sites and the O2-IMS objects are not in the TEIV export; they are on the O-Cloud inventory tab.
- The topology reads at most 500 workloads and 500 alarms (one page each); past that a note under the graph says what was cut.
- The inventory level picker shows no per-level counts: the infrastructure summary does not count FOCOM objects; each table's pager shows its total.
- There is no O1 endpoint count in the summary (`elements.total` counts managed entities, a different list), so the O1 tab shows no count.
- The config-write dialog and the software-update picker list the first 500 O1 endpoints; the dialog says when there are more.

## Troubleshooting
- Topology says "FOCOM exports an empty topology": FOCOM has no pools, managers or resources; check `/focom/topology` and FOCOM's logs.
- Every resource reads "No health data": `/focom/alarms` failed; the rest of the tree still draws.
- Counts on the NF deployment tiles show "—": the BFF summary could not ask NFO; see `/api/summary/infrastructure` `partial`.
- A table shows "Refresh failed … updated N s ago": the module stopped answering after the first page; check that module.

## Upgrade notes
- Redesign: the page moved from `pages/Infrastructure.tsx` to this folder; Topology became the default tab; lists are server-paged; the O-Cloud
  inventory gained the O2-IMS levels; the order form and the CM-write element list load only when opened.
