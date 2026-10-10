/** The Safeguards page's API knowledge (STRUCTURE.md rule 4): rApp instances and their safeguards (rApp Management), the stop switch
 * (`PUT/DELETE /rapp-mgmt/instances/{id}/kill`), limits and approval policy per invoker, the stopped list, refusals (`invoker_id`, `code`,
 * `since`) and watchers at RAN NF OAM. Counts come from a list's `total` with `limit=1` (one row read, the server counts) or the BFF summary. */
import type { Query } from "../../../api/client";
import { smo } from "../../../api/client";
import { useSmo, useSmoPage } from "../../../api/hooks";
import type { InstanceSafeguards, InstanceSummary, Page } from "../../../api/types";
import { useSummary } from "../../../data/summary";

/** rApp instances (rApp Management). */
export const INSTANCES = "/rapp-mgmt/instances";
/** Everything stopped at RAN NF OAM, by invoker id. */
export const STOPPED = "/ran-nf-oam/rapp-kill";
/** Every refusal of an rApp write. */
export const REFUSALS = "/ran-nf-oam/safeguard-refusals";
/** Watchers (webhooks) told about refusals. */
export const WATCHERS = "/ran-nf-oam/safeguard-subscriptions";
/** Limits of one invoker. */
export const limitsPath = (invokerId: string) => `/ran-nf-oam/rapp-limits/${invokerId}`;
/** Approval policy of one invoker. */
export const approvalPolicyPath = (invokerId: string) => `/ran-nf-oam/rapp-approval-policy/${invokerId}`;
/** The stop switch of one instance. */
export const killPath = (instanceId: string) => `${INSTANCES}/${instanceId}/kill`;
/** The refusal time ranges; "all" sends no `since`. */
export const REFUSAL_RANGES = { "24h": 86_400_000, "7d": 7 * 86_400_000, "30d": 30 * 86_400_000, all: 0 } as const;
/** A refusal range id. */
export type RefusalRange = keyof typeof REFUSAL_RANGES;

/** The ISO start of a refusal range counted back from `now`; undefined for "all". */
export function refusalSince(range: RefusalRange, now: number = Date.now()): string | undefined {
  return REFUSAL_RANGES[range] ? new Date(now - REFUSAL_RANGES[range]).toISOString() : undefined;
}

/** What holds one instance in check (React Query shares the call between the row's cells and the detail card). */
export function useInstanceSafeguards(instanceId: string | null) {
  return useSmo<InstanceSafeguards>(instanceId ? `${INSTANCES}/${instanceId}/safeguards` : null);
}

/** The instance count, from the BFF summary (`instances.total`). */
export function useInstanceSummary() {
  return useSummary("rapps");
}

/** A server-counted total of a list (one row read). */
function useTotal(path: string | null, query: Query = {}) {
  return useSmoPage<unknown>(path, { ...query, limit: 1 });
}

/** How many invokers are stopped. */
export function useStoppedCount() {
  return useTotal(STOPPED);
}

/** How many refusals since `since`, of every rApp. */
export function useRefusalCount(since: string) {
  return useTotal(REFUSALS, { since });
}

/** How many refusals of one invoker since `since` (null: no call). */
export function useInvokerRefusalCount(since: string, invokerId: string | null) {
  return useTotal(invokerId ? REFUSALS : null, { since, invoker_id: invokerId ?? undefined });
}

/** Every instance that can still write (not UNDEPLOYED), read page by page, for "Stop all rApp writes". */
export async function fetchStoppableInstances(pageSize = 200): Promise<InstanceSummary[]> {
  const out: InstanceSummary[] = [];
  for (let offset = 0; ; offset += pageSize) {
    const page = await smo<Page<InstanceSummary>>(INSTANCES, { query: { limit: pageSize, offset } });
    out.push(...page.items.filter((i) => i.state !== "UNDEPLOYED"));
    const more = page.total !== undefined ? offset + page.items.length < page.total : page.hasMore ?? page.items.length === pageSize;
    if (!more || page.items.length === 0) return out;
  }
}
