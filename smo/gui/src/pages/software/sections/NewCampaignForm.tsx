/** Software · new campaign (`software.new`): `POST /software-campaigns`. The elements are selected by attributes (vendorName, region,
 * entityType, tenant: every element matching all the keys given) or listed by ref. A dry run comes first (`dryRun: true` answers the waves
 * and starts nothing); Start is offered only for the exact values that were dry-run. Gated by `Can` (operator in gui-bff/app/rbac.py, which
 * also sets `requestedBy` from the signed-in user). */
import { useState } from "react";

import { useSmoAction } from "../../../api/hooks";
import { useAuth } from "../../../auth/AuthContext";
import { Can, Card, Field } from "../../../components/ui";
import { Callout } from "../../../kit/Callout";
import { Segmented } from "../../../kit/Segmented";
import { CAMPAIGNS_PATH, useVendorNames } from "../data/queries";
import type { CampaignDryRun } from "../data/types";
import { campaignBody, EMPTY_FORM, type CampaignForm } from "../data/form";

/** The form card; `onStarted` gets the new campaign's id. */
export function NewCampaignForm({ onStarted }: { onStarted: (id: string) => void }) {
  const { can } = useAuth();
  const vendors = useVendorNames();
  const [f, setF] = useState<CampaignForm>(EMPTY_FORM);
  const [preview, setPreview] = useState<{ key: string; plan: CampaignDryRun } | null>(null);
  const dry = useSmoAction();
  const start = useSmoAction();
  const built = campaignBody(f);
  const key = built.ok ? JSON.stringify(built.body) : "";
  const fresh = preview !== null && preview.key === key;
  const set = (patch: Partial<CampaignForm>) => setF({ ...f, ...patch });
  const input = (k: keyof CampaignForm, label: string, hint?: string, mono?: boolean) => (
    <Field label={label} hint={hint}><input className={mono ? "mono" : undefined} value={String(f[k])} onChange={(e) => set({ [k]: e.target.value } as Partial<CampaignForm>)} /></Field>
  );
  return (
    <Card section="software.new" title="New software campaign" sub="POST /software-campaigns · run a dry run first to see the waves">
      <Can method="POST" path={CAMPAIGNS_PATH}>
        <form className="form" onSubmit={(e) => e.preventDefault()}>
          <div className="grid g2">{input("name", "Name")}{input("softwareVersion", "Software version", "Optional", true)}</div>
          <div className="eyebrow">Which elements</div>
          <Segmented label="Target" value={f.mode} onChange={(mode) => set({ mode })}
            options={[{ id: "selector", label: "Select by attributes" }, { id: "list", label: "List elements" }]} />
          {f.mode === "selector" ? (
            <div className="grid g4">
              <Field label="Vendor">
                <input list="sw-vendors" value={f.vendorName} onChange={(e) => set({ vendorName: e.target.value })} />
                <datalist id="sw-vendors">{vendors.data?.map((v) => <option key={v.vendorName} value={v.vendorName} />)}</datalist>
              </Field>
              {input("region", "Region")}{input("entityType", "Entity type", "e.g. DU, RU")}{input("tenant", "Tenant")}
            </div>
          ) : (
            <Field label="Managed element refs" hint="One per line">
              <textarea rows={4} className="mono" value={f.elements} onChange={(e) => set({ elements: e.target.value })} spellCheck={false} />
            </Field>
          )}
          <div className="eyebrow">Waves and gate</div>
          <div className="grid g4">
            {input("waveSize", "Wave size", "Elements per wave; blank: one wave")}
            {input("wavePauseSeconds", "Pause between waves, seconds")}
            {input("gateMaxNewAlarms", "Gate: max new alarms", "More new critical/major alarms on a wave's elements fails it")}
            <Field label="On gate failure">
              <select value={f.onGateFailure} onChange={(e) => set({ onGateFailure: e.target.value as "halt" | "rollback" })}>
                <option value="halt">Halt and ask me</option><option value="rollback">Roll back automatically</option>
              </select>
            </Field>
          </div>
          {!built.ok && <div className="error-box" role="alert">{built.error}</div>}
          <div className="row end">
            <button type="button" className="btn" disabled={!built.ok || dry.isPending}
              onClick={() => built.ok && dry.mutate({ method: "POST", path: CAMPAIGNS_PATH, json: { ...built.body, dryRun: true } },
                { onSuccess: (d) => setPreview({ key, plan: d as CampaignDryRun }) })}>
              {dry.isPending ? "…" : "Dry run"}
            </button>
            <button type="button" className="btn primary" disabled={!built.ok || !fresh || start.isPending} title={fresh ? undefined : "Run a dry run of these values first"}
              onClick={() => built.ok && start.mutate({ method: "POST", path: CAMPAIGNS_PATH, json: built.body, success: "Campaign started" },
                { onSuccess: (d) => { setPreview(null); onStarted((d as { campaignId: string }).campaignId); } })}>
              {start.isPending ? "…" : "Start campaign"}
            </button>
          </div>
          {preview && (
            <Callout tone={fresh ? "volt" : "warn"} title={fresh ? "Dry run: nothing written." : "The values changed since the dry run: run it again."}>
              {preview.plan.waves.flat().length} element(s) match · {preview.plan.waveCount} wave(s) of {preview.plan.waves[0]?.length ?? 0}{preview.plan.waves.length > 1 && preview.plan.waves[preview.plan.waves.length - 1].length !== preview.plan.waves[0].length ? ` (last ${preview.plan.waves[preview.plan.waves.length - 1].length})` : ""}
              {preview.plan.waves[0]?.length ? ` · first wave: ${preview.plan.waves[0].slice(0, 5).join(", ")}${preview.plan.waves[0].length > 5 ? "…" : ""}` : ""}
            </Callout>
          )}
        </form>
      </Can>
      {!can("POST", CAMPAIGNS_PATH) && <p className="muted small">Starting a software campaign needs the operator role.</p>}
    </Card>
  );
}

