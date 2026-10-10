/** The new-campaign form of the Software page and the body of `POST /software-campaigns` it builds, with the backend's own bounds
 * (lifecycle.py `CampaignRequest`: a name, the elements named or selected (one of the two), a selector naming at least one key, whole
 * numbers for the wave size (≥ 1), pause (≥ 0), gate (≥ 0) and the optional job timeout (1 s to 7 days, MGT-15.6), `halt` or `rollback`
 * on a failed gate, and the rollback order `all` or `reverse` (MGT-15.7)). Pure, so it is unit-tested. A limit changed in RAN NF OAM's
 * validation must be changed here too (and in `lib/lifecycle.ts`), or the form accepts what the backend then refuses. */
import { MAX_JOB_TIMEOUT_SECONDS } from "../../../lib/lifecycle";

/** The form's raw values (text as typed). */
export interface CampaignForm {
  name: string; softwareVersion: string; mode: "selector" | "list";
  vendorName: string; region: string; entityType: string; tenant: string; elements: string;
  waveSize: string; wavePauseSeconds: string; gateMaxNewAlarms: string; onGateFailure: "halt" | "rollback";
  /** Seconds after which a job still running is failed (the gate then sees it); blank waits however long. */
  jobTimeoutSeconds: string;
  /** What a rollback undoes first: every wave at once, or the last wave first, then each earlier one. */
  rollbackOrder: "all" | "reverse";
}

/** A blank form: one wave, no pause, a gate of 0 new alarms that halts, no job timeout, and a rollback that undoes the last wave first (the
 * order the platform recommends for a rollout in waves; the API's own default stays "all"). */
export const EMPTY_FORM: CampaignForm = {
  name: "", softwareVersion: "", mode: "selector", vendorName: "", region: "", entityType: "", tenant: "", elements: "",
  waveSize: "", wavePauseSeconds: "0", gateMaxNewAlarms: "0", onGateFailure: "halt", jobTimeoutSeconds: "", rollbackOrder: "reverse",
};

/** The request body, or the first problem to show. `requestedBy` is left out: the GUI BFF sets it from the signed-in user. */
export function campaignBody(f: CampaignForm): { ok: true; body: Record<string, unknown> } | { ok: false; error: string } {
  if (!f.name.trim()) return { ok: false, error: "Give the campaign a name" };
  const body: Record<string, unknown> = { name: f.name.trim(), onGateFailure: f.onGateFailure, rollbackOrder: f.rollbackOrder };
  if (f.softwareVersion.trim()) body.softwareVersion = f.softwareVersion.trim();
  if (f.mode === "selector") {
    const selector: Record<string, string> = {};
    for (const k of ["vendorName", "region", "entityType", "tenant"] as const) if (f[k].trim()) selector[k] = f[k].trim();
    if (Object.keys(selector).length === 0) return { ok: false, error: "Select by at least one of vendor, region, entity type, tenant" };
    body.selector = selector;
  } else {
    const refs = [...new Set(f.elements.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean))];
    if (refs.length === 0) return { ok: false, error: "List at least one managed element" };
    body.managedElementRefs = refs;
  }
  const whole = (text: string, label: string, min: number, key: string, optional: boolean, max = Infinity): string | null => {
    const raw = text.trim();
    if (raw === "") return optional ? null : `${label} is required`;
    const n = Number(raw);
    if (!Number.isInteger(n) || n < min) return `${label} must be a whole number, at least ${min}`;
    if (n > max) return `${label} must be at most ${max}`;
    body[key] = n;
    return null;
  };
  const problem = whole(f.waveSize, "Wave size", 1, "waveSize", true) ?? whole(f.wavePauseSeconds, "Pause between waves", 0, "wavePauseSeconds", false)
    ?? whole(f.gateMaxNewAlarms, "Max new alarms", 0, "gateMaxNewAlarms", false)
    ?? whole(f.jobTimeoutSeconds, "Job timeout", 1, "jobTimeoutSeconds", true, MAX_JOB_TIMEOUT_SECONDS);
  return problem ? { ok: false, error: problem } : { ok: true, body };
}
