/** The RAN topology page's API knowledge (STRUCTURE.md rule 4): every path, query parameter and polling interval its sections read.
 * Sections call these hooks, never `useSmo` with a raw path. The module is RAN NF OAM (smo/docs/openapi/ran-nf-oam.json):
 * `/topology/links` (the neighbour relations declared in the cell guards; filters `managed_element_ref`, `link_type`, `reciprocal`, paged
 * with `limit`/`offset`), `/topology/links/counts` (the counts without the list), `/topology/relation` (how two DNs stand in the containment
 * tree), `/topology` (the TEIV export), `/topology/graph` (the containment tree with each node's open alarms, GUI-3), `/managed-entities`
 * (with `search`) and `/cell-guards`. */
import { POLL, useSmo, useSmoPage } from "../../../api/hooks";
import type { ContainmentGraph } from "./containment";
import type { CellLink, ManagedEntity } from "../../element/data/types";
import { useUrlParam } from "../../element/data/url";

const BASE = "/ran-nf-oam";

/** The kinds of relation the page calls a problem: declared one way only, pointing outside what is managed here, or at a cell id two
 * elements claim. */
export type ProblemKind = "oneway" | "external" | "ambiguous";

/** The neighbour relations route (a `kit/ServerTable` path: with `limit` it answers the page envelope). */
export const LINKS_PATH = `${BASE}/topology/links`;

/** `GET /topology/links/counts`. `notReciprocal` counts every relation not declared back, external and ambiguous ones included. */
export interface LinkCountsAnswer { total: number; notReciprocal: number; external: number; ambiguous: number; intraElement: number; interElement: number }

/** The relation counts, everywhere or around `me` (one small call; the list is never read for them). */
export function useLinkCounts(me?: string | null) {
  return useSmo<LinkCountsAnswer>(`${BASE}/topology/links/counts`, me ? { managed_element_ref: me } : undefined, { refetchInterval: POLL.inventory });
}

/** The relations declared one way between two managed cells: not reciprocal, minus the external and ambiguous ones (never reciprocal). */
export function oneWayCount(c: LinkCountsAnswer): number {
  return Math.max(0, c.notReciprocal - c.external - c.ambiguous);
}

/** The neighbour relations with `me` at either end, whole (one element's relations are few: the graph draws them all). */
export function useLinks(me?: string | null, enabled = true) {
  return useSmo<CellLink[]>(`${BASE}/topology/links`, me ? { managed_element_ref: me } : undefined,
    { refetchInterval: POLL.inventory, enabled });
}

/** Up to 20 managed elements whose ref or name contains `text` (RAN NF OAM `search`, case-insensitive), for the focus picker; none below 2 characters. */
export function useElementOptions(text: string) {
  const q = text.trim();
  return useSmo<ManagedEntity[]>(q.length >= 2 ? `${BASE}/managed-entities` : null, { search: q, limit: 20, total: false }, { refetchInterval: false, staleTime: 30_000 });
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

/** The containment graph route (scoped by the top bar's region and site cluster, `data/scope.ts` SCOPED_ROUTES). */
export const GRAPH_PATH = `${BASE}/topology/graph`;
/** The most nodes the viewer asks for: the whole tree of a small network, or of one element. */
export const GRAPH_NODES = 500;

/** The containment tree with each node's open alarms (GUI-3.1, 3.3): one element's when `me` is focused, else the network's (in the scope),
 * first `GRAPH_NODES` nodes in DN order. Polled like the alarm counts, so the overlay follows new alarms. */
export function useContainment(me: string | null) {
  return useSmo<ContainmentGraph>(GRAPH_PATH, { max_nodes: GRAPH_NODES, managed_element_ref: me ?? undefined }, { refetchInterval: POLL.lists });
}
