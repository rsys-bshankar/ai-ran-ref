/** The Configuration page's API knowledge (STRUCTURE.md rule 4): RAN NF OAM's config jobs (`/config-jobs`, `/{id}`, the
 * `continue|halt|abort|rollback|kpi-check` actions), the vendor capability registry (`/vendor-capabilities`), CM schema descriptors
 * (`/cm-schemas`), O1 adaptor endpoints and their pinned SSH host keys (`/o1-adaptor-endpoints`, `/{id}/host-keys`), element onboarding
 * (`/element-onboarding`, `/{me}/select`, `/{me}/apply`) and the KPI definitions a job's KPI guard names. Every write's `requestedBy` (and an
 * admin's MSAC tier) is set by the GUI BFF from the signed-in user (gui-bff/app/rbac.py). */
import { POLL, useSmo } from "../../../api/hooks";
import type { ConfigJob, KpiDef } from "../../../api/types";
import type { HostKey } from "../../element/data/types";
import { useUrlParam } from "../../element/data/url";

const BASE = "/ran-nf-oam";

/** The config job list route (paged, `?status=`). */
export const JOBS_PATH = `${BASE}/config-jobs`;
/** The vendor capability list route (paged). */
export const VENDORS_PATH = `${BASE}/vendor-capabilities`;
/** The CM schema list route (paged; built-in descriptors first). */
export const SCHEMAS_PATH = `${BASE}/cm-schemas`;
/** The O1 adaptor endpoint list route (paged, `?health_status=`). */
export const ENDPOINTS_PATH = `${BASE}/o1-adaptor-endpoints`;
/** The element onboarding list route (paged, `?status=`, `?software_check=`). */
export const ONBOARDING_PATH = `${BASE}/element-onboarding`;

/** The path of one job, or of one of its actions. */
export function jobPath(id: string, action?: "continue" | "halt" | "abort" | "rollback" | "kpi-check"): string {
  return `${JOBS_PATH}/${encodeURIComponent(id)}${action ? `/${action}` : ""}`;
}

/** The path of an element's onboarding action. */
export function onboardingPath(me: string, action: "select" | "apply"): string {
  return `${ONBOARDING_PATH}/${encodeURIComponent(me)}/${action}`;
}

/** One config job with its sub-changes, refreshed every 5 s while it is shown (a job moves on by itself after a pause). */
export function useJob(id: string | null) {
  return useSmo<ConfigJob>(id ? jobPath(id) : null, undefined, { refetchInterval: POLL.alarms });
}

/** The KPI definitions a KPI guard can name (the route is paged; the form offers the first 100). */
export function useKpiDefinitions() {
  return useSmo<KpiDef[]>(`${BASE}/kpi-definitions`, { limit: 100 }, { refetchInterval: POLL.inventory });
}

/** The host-key route of one endpoint (`GET`, admin `PUT` to pin; `DELETE …/{keyType}` to remove). */
export const hostKeysPath = (endpointId: string) => `${ENDPOINTS_PATH}/${encodeURIComponent(endpointId)}/host-keys`;

/** The SSH host keys pinned for one endpoint (only an `ssh` endpoint has any: the route answers 422 for another transport). */
export function useHostKeys(endpointId: string | null) {
  return useSmo<HostKey[]>(endpointId ? hostKeysPath(endpointId) : null, undefined,
    { refetchInterval: POLL.inventory, retry: false });
}

/** The selected job (`?job=`). */
export function useSelectedJob(): [string | null, (id: string | null) => void] {
  return useUrlParam("job");
}

/** The job list's state filter (`?status=`): a state, "all", or null when not chosen yet. */
export function useJobStatus(): [string | null, (status: string | null) => void] {
  return useUrlParam("status");
}
