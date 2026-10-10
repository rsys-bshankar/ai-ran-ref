/** The Decisions page's API knowledge (STRUCTURE.md rule 4): `GET /ran-nf-oam/decision-records` and its filters (`invoker_id`, `job_id`,
 * `approval_id`, `disposition`, `model_version`, `since`, `until`; paged with `total=false` — 100k records a day are only paged forward),
 * `GET /decision-records/{id}` (the record with its audit-chain integrity check), and the 24 h summary counts. The list is keyset-paged
 * (`after` / `nextCursor`, `kit/KeysetTable`) and narrowed by the global scope (`region`, `site_cluster`); the export is an asynchronous job
 * (`data/exports.ts`). */
import type { Query } from "../../../api/client";
import { useSmo } from "../../../api/hooks";
import type { DecisionRecord } from "../../../api/types";
import { useSummary } from "../../../data/summary";

/** Decision records (AI-13). */
export const DECISION_RECORDS = "/ran-nf-oam/decision-records";
/** The outcomes a record can have. */
export const DISPOSITIONS = ["DIRECT", "APPROVED", "ROLLBACK", "REJECTED", "EXPIRED", "REFUSED"] as const;
/** The time ranges of the filter bar; "all" sends no `since`. */
export const RANGES = { "1h": 3_600_000, "24h": 86_400_000, "7d": 7 * 86_400_000, all: 0 } as const;
/** A time range id. */
export type Range = keyof typeof RANGES;

/** The filter bar's values. `since` is the ISO time the range started at when it was picked (kept, so the query key does not move every render). */
export interface DecisionFilter { invoker: string; disposition: string; model: string; since: string | null; until: string; job: string; approval: string }

/** The ISO start of a range, counted back from `now`; null for "all". */
export function sinceOf(range: Range, now: number = Date.now()): string | null {
  return RANGES[range] ? new Date(now - RANGES[range]).toISOString() : null;
}

/** The list query of a filter (paging is added by the table); blank fields are left out. */
export function decisionFilterQuery(f: DecisionFilter): Query {
  const untilMs = f.until ? new Date(f.until).getTime() : NaN;
  return {
    invoker_id: f.invoker.trim() || undefined,
    disposition: f.disposition || undefined,
    model_version: f.model.trim() || undefined,
    job_id: f.job.trim() || undefined,
    approval_id: f.approval.trim() || undefined,
    since: f.since ?? undefined,
    until: Number.isNaN(untilMs) ? undefined : new Date(untilMs).toISOString(),
  };
}

/** The 24 h counts per outcome (`decisions24h.*`), counted on the server. */
export function useDecisionSummary() {
  return useSummary("decisions");
}

/** One record with its integrity check. */
export function useDecisionRecord(id: string | null | undefined) {
  return useSmo<DecisionRecord>(id ? `${DECISION_RECORDS}/${id}` : null);
}
