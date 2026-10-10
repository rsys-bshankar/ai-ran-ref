/** API knowledge of the rApps page (`pages/rapps`, STRUCTURE.md rule 4): every path, query parameter and polling interval the page's sections
 * read. The sections call the hooks here and never `useSmo` with a raw path, so an API change touches this file only. Lists the backend pages
 * are read by `kit/ServerTable` with the `*_PATH` constants below; counts come from the BFF summary (`useSummary("rapps")`, SCALE.md P2). */
import { useQuery } from "@tanstack/react-query";

import { api, ApiError } from "../../../api/client";
import { POLL, useSmo } from "../../../api/hooks";
import type { DirectoryQuery, RappDirectory } from "../../../api/rapps";
import type { FaultReport, Instance, InstanceVersions, Package, PackageArtifact, PackageUsage, PerfReport } from "../../../api/types";
import { useSummary } from "../../../data/summary";

/** The rApp instance list route (filters: `state`). */
export const INSTANCES_PATH = "/rapp-mgmt/instances";
/** The application package list route (filters: `state`). */
export const PACKAGES_PATH = "/onboarding/packages";
/** The instance states rApp Management reports, in lifecycle order (the state filter's options). */
export const INSTANCE_STATES = ["DEPLOYING", "RUNNING", "UPGRADING", "FAULTED", "UNDEPLOYED"] as const;
/** The package states Onboarding reports (the package filter's options). */
export const PACKAGE_STATES = ["ONBOARDING", "AVAILABLE", "PRIMED", "DEPRECATED", "DELETING", "FAILED"] as const;
/** How many packages a name lookup reads (the backend's page maximum). */
const NAME_LOOKUP_LIMIT = 500;

/** The page's counts: `instances.<STATE>`, `instances.total`, `packages.<STATE>`, `packages.total`. */
export const useRappsSummary = () => useSummary("rapps");

/** Package names by id, for the tables that only carry a `packageId`. One cached call shared by every row, refreshed like inventory. */
export function usePackageNames() {
  const q = useSmo<Package[]>(PACKAGES_PATH, { limit: NAME_LOOKUP_LIMIT }, { refetchInterval: POLL.inventory, staleTime: 30_000 });
  const byId = new Map((q.data ?? []).map((p) => [p.packageId, `${p.name} ${p.version}`]));
  return { name: (id: string) => byId.get(id) ?? null, query: q };
}

/** AVAILABLE packages other than `exclude`, the targets of an upgrade. */
export const useAvailablePackages = () => useSmo<Package[]>(PACKAGES_PATH, { state: "AVAILABLE", limit: NAME_LOOKUP_LIMIT });

/** The base path of one instance's routes. */
export const instanceBase = (id: string) => `${INSTANCES_PATH}/${id}`;
/** The base path of one package's routes. */
export const packageBase = (id: string) => `${PACKAGES_PATH}/${id}`;

/** One instance in full (workloadRef, configuration, pending upgrade …). */
export const useInstance = (id: string | null) => useSmo<Instance>(id ? instanceBase(id) : null);
/** The newest performance reports of an instance (bounded: a chart needs at most 50 points). */
export const usePerformance = (id: string, limit = 50) => useSmo<PerfReport[]>(`${instanceBase(id)}/performance`, { limit });
/** The newest faults of an instance (bounded). */
export const useFaults = (id: string, limit = 50) => useSmo<FaultReport[]>(`${instanceBase(id)}/faults`, { limit });
/** Committed upgrades and rollbacks of an instance, with the rollback target. */
export const useVersions = (id: string) => useSmo<InstanceVersions>(`${instanceBase(id)}/versions`);
/** The artifacts of a package. */
export const usePackageArtifacts = (id: string) => useSmo<PackageArtifact[]>(`${packageBase(id)}/artifacts`, { limit: 100 });
/** The usage registrations of a package (the cascade-delete guard). */
export const usePackageUsage = (id: string) => useSmo<PackageUsage[]>(`${packageBase(id)}/usage`, { limit: 100 });

/** The first `limit` rApps in `state` from the BFF directory (`/api/rapps?state=`, names included), for the attention strip. Keyed like
 * `api/rapps.ts` `useRappDirectory`, so a pin change refreshes it too; nothing is asked while `enabled` is false (the summary counts none). */
export function useRappsInState(state: string, limit: number, enabled: boolean) {
  const q: DirectoryQuery = { state, limit, offset: 0 };
  return useQuery<RappDirectory, ApiError>({
    queryKey: ["bff", "rapps", q],
    queryFn: ({ signal }) => api<RappDirectory>("/rapps", { query: { state, limit, offset: 0 }, signal }),
    enabled,
    refetchInterval: POLL.lists,
  });
}

/** The newest numeric metrics an instance reported (rApp Management `GET /instances/{id}/performance/latest`, GUI-9.8): `at` null and `metrics`
 * empty when it reported none. */
export interface LatestKpi { instanceId: string; at: string | null; metrics: Record<string, number> }

/** The most ids the batched read takes. */
export const LATEST_BATCH_MAX = 50;

/** The headline KPI of one instance: the first metric of its newest report (the order the rApp reported them in), or null when it reported none. */
export function headlineOf(kpi: LatestKpi | undefined): { name: string; value: number; others: number } | null {
  const entries = Object.entries(kpi?.metrics ?? {});
  if (entries.length === 0) return null;
  const [name, value] = entries[0];
  return { name, value, others: entries.length - 1 };
}

/** The newest metrics of up to 50 instances in one call (`GET /instances/performance/latest?ids=a,b`), by instance id; no call for none. */
export function useLatestKpis(ids: string[]) {
  const wanted = [...new Set(ids)].slice(0, LATEST_BATCH_MAX);
  const q = useSmo<LatestKpi[]>(wanted.length ? `${INSTANCES_PATH}/performance/latest` : null, { ids: wanted.join(",") }, { refetchInterval: POLL.lists });
  const byId = new Map((q.data ?? []).map((k) => [k.instanceId, k]));
  return { get: (id: string) => byId.get(id), query: q };
}

/** The newest metrics of one instance (the rApp detail page's headline tile). */
export const useLatestKpi = (id: string) => useSmo<LatestKpi>(`${instanceBase(id)}/performance/latest`, undefined, { refetchInterval: POLL.lists });
