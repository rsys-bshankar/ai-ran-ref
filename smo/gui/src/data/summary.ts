/** The summary counts of a console page, from the BFF's `GET /api/summary/{page}` (gui-bff/app/summary.py, SCALE.md P2).
 *
 * Every tile, badge and meter that shows a count reads it here, never by counting the rows of a list in the browser (a list is one page of at
 * most 100 rows, so a count made from it was wrong past 100). A count is `null` when its module did not answer; `partial` names those modules so
 * the box can say so (SCALE.md P10). The BFF caches each page 5 s for every user, so polling it is cheap. */
import { useQuery } from "@tanstack/react-query";

import { api, ApiError } from "../api/client";
import { POLL } from "../api/hooks";
import { LIVE_SUMMARY_POLL, useLive } from "./events";
import { KEYS } from "./keys";

/** The pages the BFF serves a summary for (gui-bff/app/summary.py `PAGES`). */
export type SummaryPage = "nav" | "dashboard" | "alarms" | "rapps" | "approvals" | "decisions" | "infrastructure" | "configuration" | "software" | "intents" | "aiml";

/** The body of `GET /api/summary/{page}`. */
export interface Summary { page: string; computedAt: string; counts: Record<string, number | null>; partial: string[] }

/** The summary of one page, refreshed every `POLL.summary` while the tab is visible, or only once a minute while the summary stream is open
 * (`data/events.ts` writes each pushed update into this same cache entry). */
export function useSummary(page: SummaryPage, opts: { enabled?: boolean; refetchInterval?: number } = {}) {
  const { connected } = useLive();
  const polled = opts.refetchInterval ?? POLL.summary;
  return useQuery<Summary, ApiError>({
    queryKey: KEYS.summary(page),
    queryFn: ({ signal }) => api<Summary>(`/summary/${page}`, { signal }),
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
