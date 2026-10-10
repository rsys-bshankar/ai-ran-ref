/** Infrastructure → O1 endpoints & jobs, the "New config write" dialog: one CM write job over the chosen managed elements, with an optional staged
 * rollout and KPI guard (call flow 03). Its element list is one bounded page of O1 endpoints, read only while the dialog is open; a note says
 * when there are more than it lists. Moved from the pre-redesign page with its fields and payload unchanged. */
import { useState } from "react";

import { useSmoAction } from "../../../api/hooks";
import { useAuth } from "../../../auth/AuthContext";
import { Field, Modal } from "../../../components/ui";
import { parseJsonObject, stagedPayload, type GuardForm, type StagedForm } from "../../../lib/domain";
import { PATHS, useEndpointChoices, useKpiDefinitions } from "../data/queries";

/** The dialog. */
export function ConfigWrite({ onClose }: { onClose: () => void }) {
  const { role } = useAuth();
  const choices = useEndpointChoices(true);
  const endpoints = choices.data?.items ?? [];
  const more = choices.data && (choices.data.total ?? 0) > endpoints.length;
  const [scope, setScope] = useState("cell");
  const [mes, setMes] = useState<string[]>([]);
  const [operation, setOperation] = useState("merge");
  const [attrs, setAttrs] = useState('{"administrativeState": "UNLOCKED"}');
  const parsed = parseJsonObject(attrs);
  const action = useSmoAction();
  const kpis = useKpiDefinitions();
  const [staged, setStaged] = useState<StagedForm>({ waveSize: "", wavePauseSeconds: "", gateMaxNewAlarms: "0", onGateFailure: "halt" });
  const [guardOn, setGuardOn] = useState(false);
  const [guard, setGuard] = useState<GuardForm>({ kpi: "", baselineMinutes: "60", observationMinutes: "60", maxRegressionPercent: "10", direction: "higher", revert: false });
  const extra = stagedPayload(staged, guardOn ? guard : null);
  return (
    <Modal title="New CM write" onClose={onClose}>
      <form className="form" onSubmit={(e) => {
        e.preventDefault();
        if (!parsed.ok || !extra.ok) return;
        // requestedBy and msacRole are set by the BFF from your GUI identity
        action.mutate({ method: "POST", path: PATHS.configJobs, success: "Config job submitted",
          json: { scope, changes: mes.map((m) => ({ managedElementRef: m, attributeChanges: parsed.value, operation })), ...extra.body } }, { onSuccess: onClose });
      }}>
        <p className="muted small">One job, decomposed into one NETCONF &lt;edit-config&gt; per managed element; mixed results aggregate to PARTIAL_SUCCESS (call flow 03).</p>
        <div className="grid cols-3 tight">
          <Field label="Managed elements" hint={more ? `The first ${endpoints.length} of ${choices.data!.total!.toLocaleString("en-US")} are listed` : "Ctrl/Cmd-click for several"}>
            <select multiple size={Math.min(5, Math.max(2, endpoints.length))} value={mes} onChange={(e) => setMes([...e.target.selectedOptions].map((o) => o.value))} required>
              {endpoints.map((e) => <option key={e.endpointId} value={e.managedElementRef}>{e.managedElementRef} ({e.healthStatus})</option>)}
            </select>
          </Field>
          <Field label="Scope" hint={scope === "entire-RAN" && role !== "admin" ? <span className="text-bad">entire-RAN needs an MSAC tier: admins only</span> : undefined}>
            <select value={scope} onChange={(e) => setScope(e.target.value)}><option>cell</option><option>site</option><option>cluster</option><option>entire-RAN</option></select>
          </Field>
          <Field label="Operation"><select value={operation} onChange={(e) => setOperation(e.target.value)}>{["merge", "replace", "create", "delete", "remove"].map((o) => <option key={o}>{o}</option>)}</select></Field>
        </div>
        <Field label="Attribute changes (JSON, applied to each)" hint={parsed.ok ? undefined : <span className="text-bad">{parsed.error}</span>}><textarea rows={4} value={attrs} onChange={(e) => setAttrs(e.target.value)} spellCheck={false} /></Field>
        <details>
          <summary>Staged rollout</summary>
          <p className="muted small">Go in waves of this many elements, and check health between them. Leave the wave size blank to write everything at once.</p>
          <div className="grid cols-3 tight">
            <Field label="Wave size" hint="Elements per wave"><input inputMode="numeric" value={staged.waveSize} onChange={(e) => setStaged({ ...staged, waveSize: e.target.value })} /></Field>
            <Field label="Pause between waves, seconds"><input inputMode="numeric" value={staged.wavePauseSeconds} onChange={(e) => setStaged({ ...staged, wavePauseSeconds: e.target.value })} /></Field>
            <Field label="New critical/major alarms allowed" hint="More than this on a wave's elements fails the gate"><input inputMode="numeric" value={staged.gateMaxNewAlarms} onChange={(e) => setStaged({ ...staged, gateMaxNewAlarms: e.target.value })} /></Field>
          </div>
          <Field label="If the gate fails"><select value={staged.onGateFailure} onChange={(e) => setStaged({ ...staged, onGateFailure: e.target.value as "halt" | "revert" })}>
            <option value="halt">Halt (an operator decides)</option><option value="revert">Undo the waves already applied</option></select></Field>
        </details>
        <details>
          <summary>KPI guard</summary>
          <label className="row gap small"><input type="checkbox" checked={guardOn} onChange={(e) => setGuardOn(e.target.checked)} /> Check a KPI after this job</label>
          {guardOn && <>
            <div className="grid cols-3 tight">
              <Field label="KPI" hint={kpis.data?.length === 0 ? "Define one on the KPIs page first" : undefined}>
                <select value={guard.kpi} onChange={(e) => setGuard({ ...guard, kpi: e.target.value })}>
                  <option value="">Choose…</option>{kpis.data?.map((k) => <option key={k.name} value={k.name}>{k.name}</option>)}
                </select>
              </Field>
              <Field label="Baseline, minutes before"><input inputMode="numeric" value={guard.baselineMinutes} onChange={(e) => setGuard({ ...guard, baselineMinutes: e.target.value })} /></Field>
              <Field label="Observe, minutes after"><input inputMode="numeric" value={guard.observationMinutes} onChange={(e) => setGuard({ ...guard, observationMinutes: e.target.value })} /></Field>
              <Field label="Regression allowed, %"><input inputMode="decimal" value={guard.maxRegressionPercent} onChange={(e) => setGuard({ ...guard, maxRegressionPercent: e.target.value })} /></Field>
              <Field label="Better is"><select value={guard.direction} onChange={(e) => setGuard({ ...guard, direction: e.target.value as "higher" | "lower" })}><option value="higher">Higher</option><option value="lower">Lower</option></select></Field>
            </div>
            <label className="row gap small"><input type="checkbox" checked={guard.revert} onChange={(e) => setGuard({ ...guard, revert: e.target.checked })} /> Roll back the elements that regressed (never over a later change)</label>
          </>}
        </details>
        {!extra.ok && <div className="error-box" role="alert">{extra.error}</div>}
        <div className="row gap end"><button type="button" className="btn" onClick={onClose}>Cancel</button><button type="submit" className="btn primary" disabled={!parsed.ok || !extra.ok || mes.length === 0 || action.isPending || (guardOn && !guard.kpi)}>Submit</button></div>
      </form>
    </Modal>
  );
}
