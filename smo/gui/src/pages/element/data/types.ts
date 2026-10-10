/** View types of the RAN NF OAM inventory reads the Element detail page uses, mapped from the module's own responses
 * (smo/ran-nf-oam/app/vendors.py `_me_view`, `query_cell_guards`; mo_tree.py `view`; main.py config history and diff; topology.py `cell_links`).
 * The RAN topology and Software pages import the shared ones (`ManagedEntity`, `CellGuard`, `CellLink`) from here, so one change to the wire
 * shape is made in one file. */

/** The class of a cell guard (vendors.py `CellClass`): how carefully every rApp must treat the cell. */
export type CellClass = "NORMAL" | "COVERAGE_CRITICAL" | "EMERGENCY";

/** The cell classes, in the order the editor offers them. */
export const CELL_CLASSES: CellClass[] = ["NORMAL", "COVERAGE_CRITICAL", "EMERGENCY"];

/** One cell's guard as stored on its element (`PUT /managed-entities/{me}/cells/{cell}/guards` body). */
export interface CellGuard { cellClass: CellClass; sectorGroup: string | null; incidentZone: string | null; neighbourRefs: string[] }

/** A row of `GET /cell-guards`: the guard with its element and cell id. */
export interface CellGuardRow extends CellGuard { managedElementRef: string; cellId: string }

/** `GET /managed-entities/{me}` (and each item of the list). */
export interface ManagedEntity {
  managedElementRef: string; managedFunctionRef: string | null; entityType: string | null; vendorName: string | null; o1Protocol: string | null;
  o1AdaptorEndpointId: string | null; supportedServices: string[] | null; conformanceMode: string | null;
  cellGuards: Record<string, CellGuard>; region: string | null; tenant: string | null;
}

/** A neighbour relation's type (topology.py `CELL_LINK_TYPES`). */
export type LinkType = "INTRA_ELEMENT" | "INTER_ELEMENT" | "AMBIGUOUS" | "EXTERNAL";

/** One declared neighbour relation of `GET /topology/links`: cell `aCell` of `aElement` lists `bCell`; `bElement` is null when the cell is not
 * managed here (EXTERNAL) or several elements declare it (AMBIGUOUS). */
export interface CellLink {
  aElement: string; aCell: string; bElement: string | null; bCell: string; linkType: LinkType;
  reciprocal: boolean; sameSectorGroup: boolean; sameIncidentZone: boolean;
}

/** One node of the containment tree (`GET /managed-objects/{dn}`, `/children`); `children` only on a `/subtree` answer. */
export interface ManagedObject {
  dn: string; parentDn: string | null; class: string; id: string; managedElementRef: string; source: "registry" | "walk" | string;
  children?: ManagedObject[];
}

/** `GET /managed-objects/{dn}/subtree`. */
export interface Subtree { tree: ManagedObject; truncated: boolean }

/** One snapshot of `GET /managed-entities/{me}/config-history` (newest first): what one dispatched write replaced and wrote. */
export interface Snapshot {
  snapshotId: string; jobId: string; subChangeStatus: string | null; managedElementRef: string; managedFunctionRef: string | null; operation: string;
  before: Record<string, unknown> | null; after: Record<string, unknown> | null; beforeError: string | null; createdAt: string;
}

/** `GET /managed-entities/{me}/config-history/diff`. */
export interface SnapshotDiff {
  managedElementRef: string; managedFunctionRef: string | null; fromSnapshot: string; toSnapshot: string;
  changed: { attribute: string; from: unknown; to: unknown }[];
  onlyInFrom: Record<string, unknown>; onlyInTo: Record<string, unknown>;
}

/** `GET /element-onboarding/{me}` (and each item of the list). */
export interface Onboarding {
  managedElementRef: string; status: string; templateName: string | null; softwareVersion: string | null; softwareBaseline: string | null;
  softwareCheck: "NOT_CHECKED" | "MATCH" | "MISMATCH" | string | null; configJobId: string | null; detail: string | null; createdAt: string | null; updatedAt: string | null;
}

/** One pinned SSH host key (`GET /o1-adaptor-endpoints/{id}/host-keys`). */
export interface HostKey { keyType: string; fingerprint: string; pinnedBy: string; pinnedAt: string }

/** The DN of an element's root object (mo_tree.py `root_dn`): a flat ref `ME-1` is `ManagedElement=ME-1`; a ref that is a DN is its own root. */
export function rootDn(me: string): string {
  return me.includes("=") ? me : `ManagedElement=${me}`;
}

/** The managed function ref of a DN below an element's root (`GNBDUFunction=1,NRCellDU=101`), or null for the root itself. */
export function functionRefOf(me: string, dn: string): string | null {
  const root = rootDn(me);
  return dn.startsWith(`${root},`) ? dn.slice(root.length + 1) : null;
}

/** The element detail route of a managed element (`/elements/<ref>`, URL-encoded), with an optional tab. */
export function elementHref(me: string, tab?: "overview" | "history" | "mo" | "guards"): string {
  return `/elements/${encodeURIComponent(me)}${tab ? `#${tab}` : ""}`;
}
