/** The summary counts of a console page, from the BFF's `GET /api/summary/{page}` (gui-bff/app/summary.py, SCALE.md P2).
 *
 * Every tile, badge and meter that shows a count reads it here, never by counting the rows of a list in the browser (a list is one page of at
 * most 100 rows, so a count made from it was wrong past 100). A count is `null` when its module did not answer; `partial` names those modules so
 * the box can say so (SCALE.md P10). The BFF caches each page 5 s for every user, so polling it is cheap.
 *
 * GUI-9.3: the global scope (`data/scope.ts`) is sent as `?region=&site_cluster=`; the answer then names in `unscoped` the counts the BFF could not
 * narrow (their module list takes no scope), and `networkWide` tells a box built on them. Each scope is its own cache entry.
 * GUI-9.8b: `useAttention` reads the Dashboard's "Needs your attention" groups in one call (`GET /api/summary/attention`). */
import { useQuery } from "@tanstack/react-query";

import { api, ApiError } from "../api/client";
import { POLL } from "../api/hooks";
import { LIVE_SUMMARY_POLL, useLive } from "./events";
import { KEYS } from "./keys";
import { isScoped, scopeKey, summaryScopeQuery, useScope } from "./scope";

/** The pages the BFF serves a summary for (gui-bff/app/summary.py `PAGES`). */
export type SummaryPage = "nav" | "dashboard" | "alarms" | "rapps" | "approvals" | "decisions" | "infrastructure" | "configuration" | "software" | "intents" | "aiml";

/** The scope an answer was computed for (null: the whole network). */
export interface SummaryScope { region: string | null; siteCluster: string | null }

/** The body of `GET /api/summary/{page}`. `unscoped`: the count keys that stayed network-wide under a scope (empty or absent without one). */
export interface Summary {
  page: string; computedAt: string; counts: Record<string, number | null>; partial: string[]; scope?: SummaryScope | null; unscoped?: string[];
  /** Small module answers a page carries whole (GUI-9.11: the Dashboard's decisions, health, worst elements, hourly alarms); null when the module did not answer. */
  panels?: Record<string, unknown>;
}

/** The summary of one page, refreshed every `POLL.summary` while the tab is visible, or only once a minute while the summary stream is open
 * (`data/events.ts` writes each pushed update into this same cache entry). */
export function useSummary(page: SummaryPage, opts: { enabled?: boolean; refetchInterval?: number } = {}) {
  const { connected } = useLive();
  const scope = useScope();
  const polled = opts.refetchInterval ?? POLL.summary;
  return useQuery<Summary, ApiError>({
    queryKey: KEYS.summary(page, scopeKey(scope)),
    queryFn: ({ signal }) => api<Summary>(`/summary/${page}`, { query: summaryScopeQuery(scope), signal }),
    refetchInterval: connected ? Math.max(polled, LIVE_SUMMARY_POLL) : polled,
    enabled: opts.enabled ?? true,
    staleTime: 4_000,
  });
}

/** One count of a summary: a number, or null while loading or when its module did not answer. */
export function count(s: Summary | undefined, key: string): number | null {
  const v = s?.counts[key];
  return typeof v === "number" ? v : null;
}

/** The sum of several counts, or null if any of them is unknown (a partial sum would read as a true total). */
export function sum(s: Summary | undefined, keys: string[]): number | null {
  let total = 0;
  for (const k of keys) {
    const v = count(s, k);
    if (v === null) return null;
    total += v;
  }
  return total;
}

/** `{STATE: n}` for every `prefix.STATE` count of a summary (`instances.RUNNING` …), leaving out `prefix.total` and zero counts. */
export function byState(s: Summary | undefined, prefix: string): Record<string, number> | undefined {
  if (!s) return undefined;
  const out: Record<string, number> = {};
  for (const [k, v] of Object.entries(s.counts)) {
    if (!k.startsWith(`${prefix}.`) || k === `${prefix}.total` || typeof v !== "number" || v === 0) continue;
    out[k.slice(prefix.length + 1)] = v;
  }
  return out;
}

/** Open RAN alarms: every alarm but the cleared ones (total − cleared, both from the same summary). */
export function openAlarms(s: Summary | undefined): number | null {
  const total = count(s, "alarms.total");
  const cleared = count(s, "alarms.cleared");
  return total === null || cleared === null ? null : Math.max(0, total - cleared);
}

/** True when a scope is set and any of `keys` (a count key, or a prefix such as "models" for every `models.*`) was not narrowed by it: the box
 * showing them says "network-wide" (`kit/ScopeNote.tsx`). */
export function networkWide(s: Pick<Summary, "unscoped"> | undefined, keys: string[]): boolean {
  const un = s?.unscoped ?? [];
  return keys.some((k) => un.some((u) => u === k || u.startsWith(`${k}.`)));
}

/** The group types of "Needs your attention" (gui-bff/app/summary.py `ATTENTION`), in the order the Dashboard shows them. */
export type AttentionType = "critical-alarms" | "approvals" | "mlmf-breaches" | "escalations";

/** One group: its true total (null when its module did not answer) and its newest rows, trimmed by the BFF to the fields the Dashboard shows. */
export interface AttentionGroup { type: AttentionType; total: number | null; items: Record<string, unknown>[] }

/** The body of `GET /api/summary/attention` and of the `attention` event. `unscoped`: the group types the scope did not narrow. */
export interface Attention { page: "attention"; computedAt?: string; groups: AttentionGroup[]; partial: string[]; scope?: SummaryScope | null; unscoped?: string[] }

/** How many rows each attention group carries (SCALE.md: at most three, then "+N more"). */
export const ATTENTION_LIMIT = 3;

/** The attention groups in one call, scoped like the summaries, re-read every 60 s, or only as a safety net while the event stream pushes them
 * (`summary:attention`, written into this same cache entry by `data/events.ts`). */
export function useAttention(opts: { enabled?: boolean } = {}) {
  const { connected } = useLive();
  const scope = useScope();
  return useQuery<Attention, ApiError>({
    queryKey: KEYS.attention(scopeKey(scope)),
    queryFn: ({ signal }) => api<Attention>("/summary/attention", { query: { limit: ATTENTION_LIMIT, ...(isScoped(scope) ? summaryScopeQuery(scope) : {}) }, signal }),
    refetchInterval: LIVE_SUMMARY_POLL,
    enabled: opts.enabled ?? true,
    staleTime: connected ? LIVE_SUMMARY_POLL : 4_000,
  });
}

/** One group of an attention answer (undefined while loading). */
export function attentionGroup(a: Attention | undefined, type: AttentionType): AttentionGroup | undefined {
  return a?.groups.find((g) => g.type === type);
}
