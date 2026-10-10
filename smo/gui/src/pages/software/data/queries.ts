/** The Software page's API knowledge (STRUCTURE.md rule 4): paths, query parameters and polling of RAN NF OAM's software campaigns
 * (`/software-campaigns`, `/{id}`, `/{id}/report`, the `continue|halt|abort|rollback` actions) and element software jobs
 * (`/software-management-jobs`), plus the vendor list the new-campaign form offers. Every action's `requestedBy` is set by the GUI BFF
 * from the signed-in user (gui-bff/app/rbac.py), so the bodies here never carry it. */
import { POLL, useSmo } from "../../../api/hooks";
import { useUrlParam } from "../../element/data/url";
import type { Campaign, CampaignReport } from "./types";

const BASE = "/ran-nf-oam";

/** The campaign list route (paged, `?status=`). */
export const CAMPAIGNS_PATH = `${BASE}/software-campaigns`;

/** The element software job list route (paged, `?managed_element_ref=`). */
export const SWM_JOBS_PATH = `${BASE}/software-management-jobs`;

/** The path of one campaign, or of one of its actions. */
export function campaignPath(id: string, action?: "continue" | "halt" | "abort" | "rollback"): string {
  return `${CAMPAIGNS_PATH}/${encodeURIComponent(id)}${action ? `/${action}` : ""}`;
}

/** One campaign, refreshed every 5 s while it is shown (it moves on by itself between waves). */
export function useCampaign(id: string | null) {
  return useSmo<Campaign>(id ? campaignPath(id) : null, undefined, { refetchInterval: POLL.alarms });
}

/** A campaign's report: totals, waves with their jobs, and what needs attention. */
export function useCampaignReport(id: string | null) {
  return useSmo<CampaignReport>(id ? `${campaignPath(id)}/report` : null, undefined, { refetchInterval: POLL.status });
}

/** The vendors with a declared capability, for the selector's vendor list (first page). */
export function useVendorNames() {
  return useSmo<{ vendorName: string }[]>(`${BASE}/vendor-capabilities`, { limit: 100 }, { refetchInterval: POLL.inventory });
}

/** The selected campaign (`?campaign=`), kept in the URL so a link opens it. */
export function useSelectedCampaign(): [string | null, (id: string | null) => void] {
  return useUrlParam("campaign");
}

/** The wave whose elements are open (`?wave=`), or null for the first. */
export function useSelectedWave(): [number | null, (wave: number) => void] {
  const [raw, set] = useUrlParam("wave");
  const n = raw === null ? NaN : Number(raw);
  return [Number.isInteger(n) ? n : null, (w: number) => set(String(w))];
}
