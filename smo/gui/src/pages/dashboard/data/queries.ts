/** Every API path, query and polling interval the Dashboard uses (STRUCTURE.md rule 4). First load: the summary counts (`/api/summary/dashboard`),
 * module health (`/api/modules/status`, shared with the sidebar's cache entry), the four "needs attention" top-3 lists, the latest decisions, the
 * fleet health by region (health score and map, one call), the worst elements and the 24 hourly alarm buckets: 10 calls, every list bounded and
 * asked with `total=false` (SCALE.md P2, P4); every aggregate is computed on the server. The model and rApp trend sparklines load only when their
 * box is opened. */
import { useQueries, useQuery } from "@tanstack/react-query";

import { api, smo } from "../../../api/client";
import { POLL, unwrapPage, useSmo } from "../../../api/hooks";
import type { Alarm, Approval, DecisionRecord, InstanceSummary, MlmfReport, ModulesStatus, PerfReport, RemedialAction } from "../../../api/types";
import { KEYS } from "../../../data/keys";
import { useSummary } from "../../../data/summary";
import type { AlarmHour, FleetHealth, WorstElement } from "./types";

/** How many items each "needs attention" group shows (SCALE.md: max 3 each, then "+N more"). */
export const ATTENTION_TOP = 3;
/** How many decisions the autonomy feed shows. */
export const FEED_SIZE = 6;
/** How many elements the "Worst DUs" ranking shows. */
export const WORST_TOP = 10;
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

/** The fleet's health (RAN NF OAM `GET /managed-entities/health`, ran-nf-oam/app/fleet.py): one group per region, or per site cluster of one
 * region, each with its element count, unhealthy count (an open critical or major alarm) and worst open severity, plus the `healthScore`
 * (100 × healthy / elements). The region level also feeds the "Network health" tile (same cache entry, no extra call). */
export const useFleetHealth = (groupBy: "region" | "site_cluster", region?: string | null, enabled = true) =>
  useSmo<FleetHealth>("/ran-nf-oam/managed-entities/health", { group_by: groupBy, region: region ?? undefined }, { refetchInterval: LIST_POLL, enabled });

/** The elements with the most open critical, then major, then any alarms (`GET /managed-entities/worst`, ranked in SQL; a bare list). */
export const useWorstElements = () =>
  useSmo<WorstElement[]>("/ran-nf-oam/managed-entities/worst", { limit: WORST_TOP }, { refetchInterval: LIST_POLL });

/** The alarms raised in each of the last 24 hours, by severity (`GET /alarms/counts?group_by=hour`, oldest first). */
export const useAlarmHours = () =>
  useSmo<{ groupBy: string; groups: AlarmHour[] }>("/ran-nf-oam/alarms/counts", { group_by: "hour" }, { refetchInterval: LIST_POLL });

/** The managed-elements route a region or site cluster drills into (a `kit/ServerTable` path; filters `?region=` and `?site_cluster=`). */
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
