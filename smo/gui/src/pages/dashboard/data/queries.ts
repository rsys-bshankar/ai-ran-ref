/** Every API path, query and polling interval the Dashboard uses (STRUCTURE.md rule 4). First load: the summary counts (`/api/summary/dashboard`),
 * module health (`/api/modules/status`, shared with the sidebar's cache entry), the four "needs attention" top-3 lists, the latest decisions and
 * one page of managed elements for the region tiles: 8 calls, every list bounded and asked with `total=false` (SCALE.md P2, P4). The model and
 * rApp trend sparklines load only when their box is opened. */
import { useQueries, useQuery } from "@tanstack/react-query";

import { api, smo } from "../../../api/client";
import { POLL, unwrapPage, useSmo } from "../../../api/hooks";
import type { Alarm, Approval, DecisionRecord, InstanceSummary, MlmfReport, ModulesStatus, PerfReport, RemedialAction } from "../../../api/types";
import { KEYS } from "../../../data/keys";
import { useSummary } from "../../../data/summary";
import type { ManagedEntity } from "./types";

/** How many items each "needs attention" group shows (SCALE.md: max 3 each, then "+N more"). */
export const ATTENTION_TOP = 3;
/** How many decisions the autonomy feed shows. */
export const FEED_SIZE = 6;
/** How many managed elements the region tiles read to learn the region names (no region list route exists). */
export const REGION_SAMPLE = 100;
/** The page size of the drill-down from a region to its elements. */
export const DRILL_PAGE = 50;

/** Polling of the bounded lists: the counts come from the summary (15 s), the lists only need to follow it loosely. */
const LIST_POLL = 60_000;
const top = { limit: ATTENTION_TOP, total: false } as const;

/** The Dashboard's summary counts (gui-bff/app/summary.py `PAGES["dashboard"]`). */
export const useDashboardSummary = () => useSummary("dashboard");

/** Health, readiness and version of every SMO module (shared cache entry with the sidebar, so no extra call). */
export function useModulesStatus() {
  return useQuery<ModulesStatus>({ queryKey: KEYS.modulesStatus, queryFn: () => api("/modules/status"), refetchInterval: POLL.status });
}

/** The newest critical RAN alarms, at most three. */
export const useCriticalAlarms = () => useSmo<Alarm[]>("/ran-nf-oam/alarms", { severity: "critical", ...top }, { refetchInterval: LIST_POLL });
/** The newest pending rApp approvals, at most three. */
export const usePendingApprovals = () => useSmo<Approval[]>("/ran-nf-oam/rapp-approvals", { status: "PENDING", ...top }, { refetchInterval: LIST_POLL });
/** The newest MLMF reports that breached their guard-KPI floor, at most three. */
export const useBreaches = () => useSmo<MlmfReport[]>("/aimgf/mlmf/reports", { breached_only: true, ...top }, { refetchInterval: LIST_POLL });
/** SA SMOS remedial actions that escalated, at most three. */
export const useEscalations = () => useSmo<RemedialAction[]>("/sa-smos/remedial-actions", { outcome: "ESCALATED", ...top }, { refetchInterval: LIST_POLL });
/** The latest decision records (newest first). */
export const useRecentDecisions = () => useSmo<DecisionRecord[]>("/ran-nf-oam/decision-records", { limit: FEED_SIZE, total: false }, { refetchInterval: LIST_POLL });

/** One page of managed elements, read for the region names on it (the list is ordered by element, so a large fleet may hide a region). */
export const useElementSample = () =>
  useSmo<ManagedEntity[]>("/ran-nf-oam/managed-entities", { limit: REGION_SAMPLE, total: false }, { refetchInterval: POLL.inventory });

/** The managed-elements route a region drills into (a `kit/ServerTable` path; the filter is `?region=`). */
export const ELEMENTS_PATH = "/ran-nf-oam/managed-entities";

/** The last 40 MLMF reports, for the model KPI sparklines; only while the trends box is open. */
export const useMlmfTrend = (enabled: boolean) => useSmo<MlmfReport[]>("/aimgf/mlmf/reports", { limit: 40, total: false }, { enabled });

/** Up to three RUNNING rApp instances, for the rApp performance sparklines; only while the trends box is open. */
export const useRunningInstances = (enabled: boolean) =>
  useSmo<InstanceSummary[]>("/rapp-mgmt/instances", { state: "RUNNING", limit: 3, total: false }, { enabled });

/** The last 30 performance reports of each instance (one call each, at most three). */
export function useInstancePerformance(instances: InstanceSummary[]) {
  return useQueries({
    queries: instances.map((i) => ({
      queryKey: ["smo", `/rapp-mgmt/instances/${i.instanceId}/performance`, { limit: 30 }],
      queryFn: async () => unwrapPage<PerfReport[]>(await smo<unknown>(`/rapp-mgmt/instances/${i.instanceId}/performance`, { query: { limit: 30 } })),
      refetchInterval: POLL.lists,
    })),
  });
}
