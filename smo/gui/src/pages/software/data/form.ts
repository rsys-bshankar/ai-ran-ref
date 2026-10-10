/** The new-campaign form of the Software page and the body of `POST /software-campaigns` it builds, with the backend's own bounds
 * (lifecycle.py `CampaignRequest`: a name, the elements named or selected (one of the two), a selector naming at least one key, whole
 * numbers for the wave size (≥ 1), pause (≥ 0) and gate (≥ 0), and `halt` or `rollback` on a failed gate). Pure, so it is unit-tested. */

/** The form's raw values (text as typed). */
export interface CampaignForm {
  name: string; softwareVersion: string; mode: "selector" | "list";
  vendorName: string; region: string; entityType: string; tenant: string; elements: string;
  waveSize: string; wavePauseSeconds: string; gateMaxNewAlarms: string; onGateFailure: "halt" | "rollback";
}

/** A blank form: one wave, no pause, a gate of 0 new alarms that halts. */
export const EMPTY_FORM: CampaignForm = {
  name: "", softwareVersion: "", mode: "selector", vendorName: "", region: "", entityType: "", tenant: "", elements: "",
  waveSize: "", wavePauseSeconds: "0", gateMaxNewAlarms: "0", onGateFailure: "halt",
};

/** The request body, or the first problem to show. `requestedBy` is left out: the GUI BFF sets it from the signed-in user. */
export function campaignBody(f: CampaignForm): { ok: true; body: Record<string, unknown> } | { ok: false; error: string } {
  if (!f.name.trim()) return { ok: false, error: "Give the campaign a name" };
  const body: Record<string, unknown> = { name: f.name.trim(), onGateFailure: f.onGateFailure };
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
  const whole = (text: string, label: string, min: number, key: string, optional: boolean): string | null => {
    const raw = text.trim();
    if (raw === "") return optional ? null : `${label} is required`;
    const n = Number(raw);
    if (!Number.isInteger(n) || n < min) return `${label} must be a whole number, at least ${min}`;
    body[key] = n;
    return null;
  };
  const problem = whole(f.waveSize, "Wave size", 1, "waveSize", true) ?? whole(f.wavePauseSeconds, "Pause between waves", 0, "wavePauseSeconds", false)
    ?? whole(f.gateMaxNewAlarms, "Max new alarms", 0, "gateMaxNewAlarms", false);
  return problem ? { ok: false, error: problem } : { ok: true, body };
}
