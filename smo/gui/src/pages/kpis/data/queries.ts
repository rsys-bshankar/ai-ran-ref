/** The KPIs & Assurance page's API knowledge (STRUCTURE.md rule 4): the RAN NF OAM KPI and PM routes, SA SMOS assurance, MDAF (legacy reports
 * and the TS 28.104 MDA functions, requests and reports), RAN Analytics producers, rApp performance and FOCOM performance; their query parameters
 * (OpenAPI `GET` parameters only) and the time windows of the KPI computations. Sections call the hooks here, never `useSmo` with a raw path.
 *
 * The Overview's tiles compute standard KPIs on the server (`GET /ran-nf-oam/kpis/{name}?from_time&group_by=all`, MGT-11): one call per tile,
 * since there is no batch KPI route. A KPI that is not defined answers 404 and its tile shows "—". */
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { smo, type ApiError, type Query } from "../../../api/client";
import { POLL, useSmo, useSmoPage } from "../../../api/hooks";
import type { AnalyticsProducer, CoordinationGroup, DmeType, InstanceSummary, KpiDef, KpiScheduleRow, Monitor, O1Endpoint, PerfReport, RemedialAction, ServiceOrder } from "../../../api/types";
import type { KpiResult } from "./types";

/** KPI definitions (RAN NF OAM). */
export const KPI_DEFINITIONS = "/ran-nf-oam/kpi-definitions";
/** KPI schedules (publish a KPI to DME on a timer; the route is not paged). */
export const KPI_SCHEDULES = "/ran-nf-oam/kpi-schedules";
/** PM subscriptions (`managed_element_ref`). */
export const PM_SUBSCRIPTIONS = "/ran-nf-oam/pm-subscriptions";
/** O1 endpoints, for the PM form's element picker. */
export const O1_ENDPOINTS = "/ran-nf-oam/o1-adaptor-endpoints";
/** SA SMOS assurance monitors (no filter: "breaching" is not a server filter). */
export const MONITORS = "/sa-smos/monitors";
/** SA SMOS remedial actions (`monitor_id`, `outcome`). */
export const REMEDIAL_ACTIONS = "/sa-smos/remedial-actions";
/** Legacy MDAF analytics reports (`analytics_type`). */
export const MDAF_REPORTS = "/mdaf/reports";
/** Legacy MDAF analytics subscriptions. */
export const MDAF_SUBSCRIPTIONS = "/mdaf/subscriptions";
/** TS 28.104 MDA functions (feature 9). */
export const MDA_FUNCTIONS = "/mdaf/mda-functions";
/** TS 28.104 MDA requests (`requested_by`; feature 9). */
export const MDA_REQUESTS = "/mdaf/mda-requests";
/** TS 28.104 MDA reports (`mda_type`, `report_kind`, `mda_request_id`, `managed_entity`; feature 9). */
export const MDA_REPORTS = "/mdaf/mda-reports";
/** RAN Analytics producers (`analytics_type`). */
export const PRODUCERS = "/ran-analytics/producers";
/** FOCOM O-Cloud performance metrics (`resource_ref`). */
export const OCLOUD_PERFORMANCE = "/focom/performance";
/** rApp instances (the performance tab's picker, the monitor form's scopes). */
export const INSTANCES = "/rapp-mgmt/instances";
/** The report kinds MDAF types its reports with. */
export const REPORT_KINDS = ["ANALYTICS", "PREDICTION", "DRIFT"] as const;
/** How an MDA request's reports are delivered (TS 28.104 reportingMethod). */
export const REPORTING_METHODS = ["FILE", "STREAMING", "NOTIFICATION"] as const;

/** Where the browser downloads one MDA report's file (the BFF proxies `/api/smo/<module>/...`). */
export const mdaReportFileHref = (id: string) => `/api/smo${MDA_REPORTS}/${id}/file`;
/** One rApp instance's performance reports. */
export const rappPerformancePath = (id: string) => `${INSTANCES}/${id}/performance`;
/** One monitor's base path (`/evaluate`, `/escalate`, `/remedial-actions`). */
export const monitorPath = (id: string) => `${MONITORS}/${id}`;

/** The time ranges of the Overview. */
export type Range = "1h" | "24h" | "7d";
/** A range's length in hours. */
export const RANGE_HOURS: Record<Range, number> = { "1h": 1, "24h": 24, "7d": 168 };

/** The window start for a range, as an ISO time rounded down to the minute so the query key (and the cache) is stable within a minute. */
export function windowStart(range: Range, now = Date.now()): string {
  const t = now - RANGE_HOURS[range] * 3_600_000;
  return new Date(t - (t % 60_000)).toISOString();
}

/** One KPI computed over the range, `group_by`: "all" (one value) or "element" (one per managed element). */
export function useKpi(name: string, range: Range, groupBy: "all" | "element" = "all") {
  const from = useMemo(() => windowStart(range), [range]);
  const path = `/ran-nf-oam/kpis/${name}`;
  const q: Query = { from_time: from, group_by: groupBy };
  // Not `useSmo`: it unwraps any `{items: [...]}` body to the bare list, and a KPI result is such a body with its unit and window around it.
  return useQuery<KpiResult, ApiError>({
    queryKey: ["smo", path, "kpi", q], queryFn: ({ signal }) => smo<KpiResult>(path, { query: q, signal }),
    refetchInterval: POLL.inventory, retry: false,
  });
}

/** The newest escalated remedial actions (the "Escalated to you" callout), with the true count of them. */
export function useEscalations(limit = 5) {
  return useSmoPage<RemedialAction>(REMEDIAL_ACTIONS, { outcome: "ESCALATED", limit });
}

/** Every monitor in one read (≤ 500), only to name a remedial action's scope in the actions table. */
export function useMonitorIndex() {
  return useSmoPage<Monitor>(MONITORS, { limit: 500 }, { refetchInterval: POLL.inventory });
}

/** The remedial actions of one monitor (only the count is used: `total`). */
export function useMonitorActionCount(monitorId: string) {
  return useSmoPage<RemedialAction>(REMEDIAL_ACTIONS, { monitor_id: monitorId, limit: 1 });
}

/** KPI definitions and schedules (Definitions tab; both bounded at 200 as before). */
export function useKpiDefinitions() {
  return useSmo<KpiDef[]>(KPI_DEFINITIONS, { limit: 200 });
}
/** KPI schedules. */
export function useKpiSchedules() {
  return useSmo<KpiScheduleRow[]>(KPI_SCHEDULES, { limit: 200 });
}

/** rApp instances (pickers). */
export function useInstances() {
  return useSmo<InstanceSummary[]>(INSTANCES, { limit: 200 });
}
/** One instance's performance reports (bounded at 100). */
export function useRappPerformance(id: string) {
  return useSmo<PerfReport[]>(id ? rappPerformancePath(id) : null, { limit: 100 });
}
/** O1 endpoints (PM form). */
export function useO1Endpoints() {
  return useSmo<O1Endpoint[]>(O1_ENDPOINTS, { limit: 200 });
}
/** Analytics producers (Analytics tab and its type pickers). */
export function useProducers() {
  return useSmo<AnalyticsProducer[]>(PRODUCERS, { limit: 200 });
}
/** DME types (producer form). */
export function useDmeTypes() {
  return useSmo<DmeType[]>("/dme/dme-types");
}
/** SO SMOS orders and model coordination groups (the monitor form's scopes). */
export function useOrders() {
  return useSmo<ServiceOrder[]>("/so-smos/orders", { limit: 200 });
}
/** Model coordination groups. */
export function useCoordinationGroups() {
  return useSmo<CoordinationGroup[]>("/mlmr/coordination-groups", { limit: 200 });
}
