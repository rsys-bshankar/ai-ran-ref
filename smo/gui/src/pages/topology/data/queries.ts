/** The RAN topology page's API knowledge (STRUCTURE.md rule 4): every path, query parameter and polling interval its sections read.
 * Sections call these hooks, never `useSmo` with a raw path. The module is RAN NF OAM (smo/docs/openapi/ran-nf-oam.json):
 * `/topology/links` (the neighbour relations declared in the cell guards, unpaged: the route takes only `managed_element_ref` and
 * `link_type`), `/topology/relation` (how two DNs stand in the containment tree), `/topology` (the TEIV export), `/managed-entities` and
 * `/cell-guards`. Two sections reading the same links share one cache entry (same path + query), so the tiles, the problem table and the
 * graph cost one call each per distinct filter. */
import { POLL, useSmo, useSmoPage } from "../../../api/hooks";
import type { CellLink, ManagedEntity } from "../../element/data/types";
import { useUrlParam } from "../../element/data/url";

const BASE = "/ran-nf-oam";

/** The kinds of relation the page calls a problem: declared one way only, pointing outside what is managed here, or at a cell id two
 * elements claim. */
export type ProblemKind = "oneway" | "external" | "ambiguous";

/** The neighbour relations, optionally only those with `me` at either end. The route answers the whole list (no paging). */
export function useLinks(me?: string | null, enabled = true) {
  return useSmo<CellLink[]>(`${BASE}/topology/links`, me ? { managed_element_ref: me } : undefined,
    { refetchInterval: POLL.inventory, enabled });
}

/** The managed elements of the first page (for the focus picker's suggestions; the route has no name search). */
export function useElementOptions() {
  return useSmo<ManagedEntity[]>(`${BASE}/managed-entities`, { limit: 100 }, { refetchInterval: POLL.inventory });
}

/** One managed element with its cell guards, or nothing when none is focused. */
export function useEntity(me: string | null) {
  return useSmo<ManagedEntity>(me ? `${BASE}/managed-entities/${encodeURIComponent(me)}` : null, undefined,
    { refetchInterval: POLL.inventory, retry: false });
}

/** The true count of registered elements: a one-row page with its `total`. */
export function useElementCount() {
  return useSmoPage<ManagedEntity>(`${BASE}/managed-entities`, { limit: 1 }, { refetchInterval: POLL.inventory });
}

/** The true count of cells that carry a guard: a one-row page of `/cell-guards` with its `total`. */
export function useGuardedCellCount() {
  return useSmoPage<unknown>(`${BASE}/cell-guards`, { limit: 1 }, { refetchInterval: POLL.inventory });
}

/** The answer of `/topology/relation` for two DNs (404 when either is not in the containment tree). Runs only once both are given. */
export function useRelation(a: string, b: string) {
  return useSmo<{ a: string; b: string; relation: string }>(a && b ? `${BASE}/topology/relation` : null, { a, b },
    { refetchInterval: false, retry: false });
}

/** The path of the TEIV-shaped export of the containment tree (`GET /topology`), optionally of one element. */
export const TOPOLOGY_EXPORT_PATH = `${BASE}/topology`;

/** The page's URL state: the focused element (`?me=`) and the problem filter (`?problem=`), so a link or a reload lands on the same view. */
export function useTopologyParams() {
  const [me, setMe] = useUrlParam("me");
  const [raw, setRaw] = useUrlParam("problem");
  const problem: ProblemKind = raw === "external" || raw === "ambiguous" ? raw : "oneway";
  return { me, problem, setMe, setProblem: (v: ProblemKind) => setRaw(v === "oneway" ? null : v) };
}
