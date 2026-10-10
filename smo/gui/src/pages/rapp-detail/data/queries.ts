/** API knowledge of the rApp detail page (`pages/rapp-detail`, route /rapps/<instance>, STRUCTURE.md rule 4): every path and query parameter
 * its sections read. Several sections read the same instance, safeguards or performance query; they share one cache entry, so each is one
 * call per page. Instance-level reads that the rApps page also uses (versions, performance) come from `pages/rapps/data/queries.ts`. */
import { useSmo, useSmoPage } from "../../../api/hooks";
import type { DecisionRecord, FaultReport, InstanceSafeguards, NfDeployment, Package, PackageUsage, PerfReport } from "../../../api/types";
import { instanceBase, packageBase, useInstance } from "../../rapps/data/queries";

export { useInstance };

/** What stops or limits the rApp at RAN NF OAM (invoker id, kill, limits, approval policy). */
export const useSafeguards = (id: string) => useSmo<InstanceSafeguards>(`${instanceBase(id)}/safeguards`);
/** The newest performance reports (bounded to 50: a sparkline needs no more, SCALE.md P12). */
export const usePerformance = (id: string) => useSmo<PerfReport[]>(`${instanceBase(id)}/performance`, { limit: 50 });
/** The faults list route of the instance, paged by `kit/ServerTable`. */
export const faultsPath = (id: string) => `${instanceBase(id)}/faults`;
/** The newest faults (bounded), for the lifecycle flows' evaluators. */
export const useRecentFaults = (id: string) => useSmo<FaultReport[]>(faultsPath(id), { limit: 50 });

/** The decision records route. */
export const DECISIONS_PATH = "/ran-nf-oam/decision-records";
/** The latest `limit` decisions of one invoker, without a total (the cheaper page). */
export const useRecentDecisions = (invokerId: string | null, limit = 10) =>
  useSmoPage<DecisionRecord>(invokerId ? DECISIONS_PATH : null, { invoker_id: invokerId ?? undefined, limit, total: false });
/** The number of decisions of one invoker in the last 24 h (`total` of a one-row page). `since` is rounded to the minute so the key is stable. */
export function useDecisionCount24h(invokerId: string | null) {
  const since = new Date(Math.floor((Date.now() - 24 * 3600_000) / 60_000) * 60_000).toISOString();
  return useSmoPage<DecisionRecord>(invokerId ? DECISIONS_PATH : null, { invoker_id: invokerId ?? undefined, since, limit: 1 });
}

/** The package's onboarding state (`/onboarding-status`: state, descriptor id; no name — the caller adds it). */
export const usePackageStatus = (packageId: string | null) =>
  useSmo<Pick<Package, "packageId" | "state" | "nfDeploymentDescriptorId">>(packageId ? `${packageBase(packageId)}/onboarding-status` : null);
/** The usage registrations of the package (flow 06's cascade-delete guard). */
export const usePackageUsage = (packageId: string | null) => useSmo<PackageUsage[]>(packageId ? `${packageBase(packageId)}/usage` : null, { limit: 100 });
/** The NFO deployment of the instance (`workloadRef`). */
export const useDeployment = (workloadRef: string | null | undefined) => useSmo<NfDeployment>(workloadRef ? `/nfo/deployments/${workloadRef}` : null);
