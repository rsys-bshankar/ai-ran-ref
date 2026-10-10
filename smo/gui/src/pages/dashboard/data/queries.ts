/** Every API path, query and polling interval the Dashboard uses (STRUCTURE.md rule 4). First load: three calls (GUI-9.11). The summary
 * (`/api/summary/dashboard`) carries the counts and, under `panels`, the latest decisions, the fleet health by region (health score and map), the
 * worst elements and the 24 hourly alarm buckets, fetched and cached 5 s by the BFF for every user (gui-bff/app/summary.py `PANELS`); the "needs
 * attention" groups are one call (`/api/summary/attention`, GUI-9.8b); module health (`/api/modules/status`) is the shell's read (the sidebar's
 * environment chip, same cache entry). Every aggregate is computed on the server (SCALE.md P2, P4). A BFF that answers without `panels` (an older
 * one) or a summary that failed makes each box read its module directly, as before. The site-cluster drill-down, the model and rApp trend
 * sparklines load only when opened. Every read is narrowed by the global scope where its route takes one (`data/scope.ts`). */
import { useQueries, useQuery } from "@tanstack/react-query";

import { api, smo, type Query } from "../../../api/client";
import { POLL, unwrapPage, useSmo } from "../../../api/hooks";
import type { DecisionRecord, InstanceSummary, MlmfReport, ModulesStatus, PerfReport } from "../../../api/types";
import { KEYS } from "../../../data/keys";
import { useAttention, useSummary } from "../../../data/summary";
import type { AlarmHour, FleetHealth, WorstElement } from "./types";

/** How many decisions the autonomy feed shows. */
export const FEED_SIZE = 6;
/** How many elements the "Worst DUs" ranking shows. */
export const WORST_TOP = 10;
/** The page size of the drill-down from a region to its elements. */
export const DRILL_PAGE = 50;

/** Polling of the bounded lists: the counts come from the summary (15 s), the lists only need to follow it loosely. */
const LIST_POLL = 60_000;

/** The Dashboard's summary counts (gui-bff/app/summary.py `PAGES["dashboard"]`). */
export const useDashboardSummary = () => useSummary("dashboard");

/** Health, readiness and version of every SMO module (shared cache entry with the sidebar, so no extra call). */
export function useModulesStatus() {
  return useQuery<ModulesStatus>({ queryKey: KEYS.modulesStatus, queryFn: () => api("/modules/status"), refetchInterval: POLL.status });
}

/** "Needs your attention": critical alarms, pending approvals, MLMF breaches and escalations, the newest three of each with their totals, in one
 * BFF call (`GET /api/summary/attention?limit=3`, pushed as `event: attention` while the stream is open). */
export const useNeedsAttention = () => useAttention();
/** A box's data as `kit/states.tsx` `QueryState` reads it. */
export interface PanelQuery<T> { data: T | undefined; isLoading: boolean; error: Error | null; refetch: () => unknown; dataUpdatedAt: number }

/** One panel of the Dashboard summary (GUI-9.11), or the module's own route when the summary carries no `panels` (an older BFF, or the summary
 * call failed). A panel that is null (its module did not answer) reads as an error whose retry refetches the summary. `pick` turns the module's
 * answer into what the box draws (the decisions' page envelope into its rows). */
function usePanel<T>(name: string, path: string, params: Query, pick: (raw: unknown) => T = (raw) => raw as T): PanelQuery<T> {
  const summary = useDashboardSummary();
  const panels = summary.data?.panels;
  const direct = useSmo<unknown>(path, params, { refetchInterval: LIST_POLL, enabled: (summary.isSuccess || summary.isError) && panels === undefined });
  if (panels === undefined && (summary.isError || summary.isSuccess)) {
    return { data: direct.data === undefined ? undefined : pick(direct.data), isLoading: direct.isLoading, error: direct.error, refetch: direct.refetch, dataUpdatedAt: direct.dataUpdatedAt };
  }
  const raw = panels?.[name];
  const missing = panels !== undefined && (raw === null || raw === undefined);
  return {
    data: raw === null || raw === undefined ? undefined : pick(raw),
    isLoading: summary.isLoading,
    error: missing ? new Error("RAN NF OAM did not answer.") : summary.error,
    refetch: summary.refetch,
    dataUpdatedAt: summary.dataUpdatedAt,
  };
}

/** The latest decision records (newest first): the summary's `decisions` panel, `/decision-records?limit=6&total=false` (a page envelope). */
export const useRecentDecisions = () =>
  usePanel<DecisionRecord[]>("decisions", "/ran-nf-oam/decision-records", { limit: FEED_SIZE, total: false }, (raw) => unwrapPage<DecisionRecord[]>(raw));

/** The fleet's health (RAN NF OAM `GET /managed-entities/health`, ran-nf-oam/app/fleet.py): one group per region, or per site cluster of one
 * region, each with its element count, unhealthy count (an open critical or major alarm) and worst open severity, plus the `healthScore`
 * (100 × healthy / elements). The region level is the summary's `health` panel (the map and the "Network health" tile share it); a region's
 * site clusters are read directly when the operator opens it. */
export function useFleetHealth(groupBy: "region" | "site_cluster", region?: string | null, enabled = true): PanelQuery<FleetHealth> {
  const regionLevel = groupBy === "region";
  const panel = usePanel<FleetHealth>("health", "/ran-nf-oam/managed-entities/health", { group_by: "region" });
  const drill = useSmo<FleetHealth>("/ran-nf-oam/managed-entities/health", { group_by: groupBy, region: region ?? undefined },
    { refetchInterval: LIST_POLL, enabled: enabled && !regionLevel });
  return regionLevel ? panel : { data: drill.data, isLoading: drill.isLoading, error: drill.error, refetch: drill.refetch, dataUpdatedAt: drill.dataUpdatedAt };
}

/** The elements with the most open critical, then major, then any alarms (the summary's `worst` panel, `GET /managed-entities/worst?limit=10`). */
export const useWorstElements = () => usePanel<WorstElement[]>("worst", "/ran-nf-oam/managed-entities/worst", { limit: WORST_TOP });

/** The alarms raised in each of the last 24 hours, by severity (the summary's `alarmHours` panel, `GET /alarms/counts?group_by=hour`, oldest first). */
export const useAlarmHours = () =>
  usePanel<{ groupBy: string; groups: AlarmHour[] }>("alarmHours", "/ran-nf-oam/alarms/counts", { group_by: "hour" });

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
