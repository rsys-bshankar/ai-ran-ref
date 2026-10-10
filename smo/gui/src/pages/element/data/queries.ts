/** The Element detail page's API knowledge (STRUCTURE.md rule 4): RAN NF OAM reads and writes of one managed element. The element
 * (`/managed-entities/{me}`), its alarms (counts from a one-row page's `total`), its config history (`/config-history`, offset-paged; the route
 * has no keyset cursor) and the diff of two snapshots, its live configuration (`/config`, read from the NF), the containment tree
 * (`/managed-objects/{dn}`, `/children?limit=` on expand, `/subtree`, `POST …/managed-objects/refresh`), its cell guards (`/cell-guards`,
 * `PUT|DELETE /managed-entities/{me}/cells/{cell}/guards`), its neighbour relations, onboarding row and pinned host keys. */
import { POLL, useSmo, useSmoPage } from "../../../api/hooks";
import type { CellLink, HostKey, ManagedEntity, ManagedObject, Onboarding, Snapshot, SnapshotDiff, Subtree } from "./types";
import { useUrlParam } from "./url";

const BASE = "/ran-nf-oam";
const enc = encodeURIComponent;

/** The path of one element. */
export const entityPath = (me: string) => `${BASE}/managed-entities/${enc(me)}`;
/** The guard route of one cell of an element (PUT sets it, DELETE removes it). */
export const guardPath = (me: string, cell: string) => `${entityPath(me)}/cells/${enc(cell)}/guards`;
/** The cell guard query route (paged, `?managed_element_ref=`). */
export const GUARDS_PATH = `${BASE}/cell-guards`;
/** The route that re-reads an element's objects from its server. */
export const refreshPath = (me: string) => `${entityPath(me)}/managed-objects/refresh`;

/** The element with its cell guards; 404 when it is not registered. */
export function useEntity(me: string) {
  return useSmo<ManagedEntity>(entityPath(me), undefined, { refetchInterval: POLL.inventory, retry: false });
}

/** The true count of the element's alarms (all, or of one severity): a one-row page's `total`. */
export function useAlarmCount(me: string, severity?: string) {
  return useSmoPage<unknown>(`${BASE}/alarms`, { managed_element_ref: me, ...(severity ? { severity } : {}), limit: 1 }, { refetchInterval: POLL.status });
}

/** One page of the element's config history, newest first. */
export function useHistory(me: string, query: { limit: number; offset: number; managed_function_ref?: string }) {
  return useSmoPage<Snapshot>(`${entityPath(me)}/config-history`, query, { refetchInterval: POLL.lists });
}

/** The diff of two snapshots of one managed object (422 when they are of different functions). */
export function useDiff(me: string, from: string | null, to: string | null) {
  return useSmo<SnapshotDiff>(from && to ? `${entityPath(me)}/config-history/diff` : null, { from_snapshot: from ?? "", to_snapshot: to ?? "" },
    { refetchInterval: false, retry: false });
}

/** One node of the containment tree. */
export function useMo(dn: string | null) {
  return useSmo<ManagedObject>(dn ? `${BASE}/managed-objects/${enc(dn)}` : null, undefined, { refetchInterval: POLL.inventory, retry: false });
}

/** The first `limit` children of a node, loaded only while it is open (`enabled`). */
export function useChildren(dn: string, limit: number, enabled: boolean) {
  return useSmoPage<ManagedObject>(`${BASE}/managed-objects/${enc(dn)}/children`, { limit }, { refetchInterval: false, enabled });
}

/** A node and its descendants `depth` levels down (at most 1000 nodes; `truncated` says when that cut it), fetched on request. */
export function useSubtree(dn: string | null, depth: number) {
  return useSmo<Subtree>(dn ? `${BASE}/managed-objects/${enc(dn)}/subtree` : null, { depth }, { refetchInterval: false, retry: false });
}

/** The live configuration of the element or one of its functions, read from the NF (on request: it is a southbound read). */
export function useLiveConfig(me: string, functionRef: string | null, enabled: boolean) {
  return useSmo<{ attributes: Record<string, unknown> }>(`${entityPath(me)}/config`, functionRef ? { managed_function_ref: functionRef } : undefined,
    { refetchInterval: false, retry: false, enabled });
}

/** The neighbour relations with this element at either end (the route is not paged). */
export function useElementLinks(me: string) {
  return useSmo<CellLink[]>(`${BASE}/topology/links`, { managed_element_ref: me }, { refetchInterval: POLL.inventory });
}

/** The element's onboarding row (404 when it was not onboarded through a template). */
export function useOnboarding(me: string) {
  return useSmo<Onboarding>(`${BASE}/element-onboarding/${enc(me)}`, undefined, { refetchInterval: POLL.inventory, retry: false });
}

/** The SSH host keys pinned for the element's O1 endpoint (422 when the endpoint is not ssh). */
export function useHostKeys(endpointId: string | null) {
  return useSmo<HostKey[]>(endpointId ? `${BASE}/o1-adaptor-endpoints/${enc(endpointId)}/host-keys` : null, undefined,
    { refetchInterval: POLL.inventory, retry: false });
}

/** The selected managed object (`?mo=`), the two snapshots compared (`?from=`, `?to=`) and the cell whose guard is edited (`?cell=`). */
export const useSelectedMo = () => useUrlParam("mo");
/** The older snapshot of the comparison. */
export const useFromSnapshot = () => useUrlParam("from");
/** The newer snapshot of the comparison. */
export const useToSnapshot = () => useUrlParam("to");
/** The cell whose guard is open in the editor ("+" for a new one). */
export const useEditedCell = () => useUrlParam("cell");
