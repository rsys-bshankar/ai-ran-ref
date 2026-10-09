import { useState } from "react";

import { useSmo, useSmoAction } from "../api/hooks";
import type { CampaignPreview, CampaignReport, CampaignStatus, CampaignSummary, O1Endpoint } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { ActionButton, Can, Card, DataTable, Drawer, ErrorBox, Field, Id, KeyValue, Modal, StateBadge } from "../components/ui";
import { formatTime } from "../lib/domain";
import {
  blankCampaign, campaignActions, campaignPayload, CAMPAIGN_MEANING, describeHalt, describeSelector, describeSettings, waveProgress, type CampaignForm,
} from "../lib/lifecycle";
import { LifecycleWatchers } from "./LifecycleWatchers";

const CAMPAIGNS = "/ran-nf-oam/software-campaigns";
const STATUSES: CampaignStatus[] = ["PENDING", "RUNNING", "HALTED", "COMPLETED", "ABORTED", "ROLLING_BACK", "ROLLED_BACK", "ROLLBACK_FAILED"];

/** Software campaigns (PR-MGT-15, GUI step 15.5): a software change over many elements, in waves with a health gate between them. Reads are open to a viewer; starting
 * one and halting, continuing, aborting or rolling it back are an operator's actions, recorded under the signed-in user. */
export function CampaignsTab() {
  const [status, setStatus] = useState<CampaignStatus | "">("");
  const [starting, setStarting] = useState(false);
  const [open, setOpen] = useState<string | null>(null);
  const campaigns = useSmo<CampaignSummary[]>(CAMPAIGNS, { status: status || undefined, limit: 200 }, { refetchInterval: 5_000 });
  return (
    <>
      <Card title="Software campaigns" actions={<>
        <select value={status} onChange={(e) => setStatus(e.target.value as CampaignStatus | "")} aria-label="Campaign status"><option value="">All states</option>{STATUSES.map((s) => <option key={s}>{s}</option>)}</select>
        <Can method="POST" path={CAMPAIGNS}><button className="btn primary" onClick={() => setStarting(true)}>Start a campaign…</button></Can>
      </>}>
        <p className="muted small">
          A campaign starts one software management job for each element of a wave, together; when every job of the wave has ended a health gate decides whether the next wave starts.
          A campaign orders, gates and records the software jobs; in this build the element's own report advances each job, so it does not yet tell an element which software to install.
        </p>
        <DataTable rows={campaigns.data} rowKey={(c) => c.campaignId} error={campaigns.error} loading={campaigns.isLoading} empty="No campaign has been started." onRowClick={(c) => setOpen(c.campaignId)} columns={[
          { header: "Campaign", render: (c) => <><strong>{c.name}</strong> <Id value={c.campaignId} /></> },
          { header: "State", render: (c) => <span title={CAMPAIGN_MEANING[c.status]}><StateBadge state={c.status} /></span> },
          { header: "Progress", render: (c) => waveProgress(c) },
          { header: "Software", render: (c) => c.softwareVersion ?? <span className="muted">—</span> },
          { header: "Why held", render: (c) => (c.haltedReason ? <span className="small">{describeHalt(c.haltedReason)}</span> : <span className="muted">—</span>) },
          { header: "Started", render: (c) => formatTime(c.createdAt) },
        ]} />
      </Card>
      {starting && <StartCampaign onClose={() => setStarting(false)} onStarted={(id) => { setStarting(false); if (id) setOpen(id); }} />}
      {open && <CampaignDrawer id={open} onClose={() => setOpen(null)} />}
      <LifecycleWatchers />
    </>
  );
}

// ---------------------------------------------------------------- start

function StartCampaign({ onClose, onStarted }: { onClose: () => void; onStarted: (campaignId: string | null) => void }) {
  const endpoints = useSmo<O1Endpoint[]>("/ran-nf-oam/o1-adaptor-endpoints", { limit: 200 });
  const [form, setForm] = useState<CampaignForm>(blankCampaign());
  const [problem, setProblem] = useState<string | null>(null);
  const [preview, setPreview] = useState<CampaignPreview | null>(null);
  const action = useSmoAction();
  const set = <K extends keyof CampaignForm>(key: K, value: CampaignForm[K]) => { setPreview(null); setForm((f) => ({ ...f, [key]: value })); };
  const run = (dryRun: boolean) => {
    const parsed = campaignPayload(form, dryRun);
    if (!parsed.ok) return setProblem(parsed.error);
    setProblem(null);
    if (dryRun) action.mutate({ method: "POST", path: CAMPAIGNS, json: parsed.body }, { onSuccess: (data) => setPreview(data as CampaignPreview) });
    else action.mutate({ method: "POST", path: CAMPAIGNS, json: parsed.body, success: "Campaign started: the first wave's software jobs are running" },
      { onSuccess: (data) => onStarted((data as CampaignSummary | undefined)?.campaignId ?? null) });
  };
  return (
    <Modal title="Start a software campaign" onClose={onClose}>
      <div className="form">
        <p className="muted small">The elements are fixed now and cut into waves. An element that already has a software job running refuses the whole campaign. It is recorded under your name.</p>
        <div className="grid cols-3 tight">
          <Field label="Name"><input value={form.name} maxLength={200} onChange={(e) => set("name", e.target.value)} autoFocus /></Field>
          <Field label="Software version" hint="Optional; kept with each job"><input value={form.softwareVersion} maxLength={100} onChange={(e) => set("softwareVersion", e.target.value)} /></Field>
        </div>
        <fieldset className="field">
          <legend className="field-label">Which elements</legend>
          <label className="row gap small"><input type="radio" name="campaign-mode" checked={form.mode === "named"} onChange={() => set("mode", "named")} /> These elements</label>
          <label className="row gap small"><input type="radio" name="campaign-mode" checked={form.mode === "selector"} onChange={() => set("mode", "selector")} /> Every element that matches</label>
        </fieldset>
        {form.mode === "named" ? (
          <Field label="Managed elements" hint="Ctrl/Cmd-click for several; in the order chosen they are cut into waves by reference">
            <select multiple size={Math.min(6, Math.max(3, endpoints.data?.length ?? 3))} value={form.refs} aria-label="Managed elements" onChange={(e) => set("refs", [...e.target.selectedOptions].map((o) => o.value))}>
              {endpoints.data?.map((e) => <option key={e.endpointId} value={e.managedElementRef}>{e.managedElementRef} ({e.healthStatus})</option>)}
            </select>
          </Field>
        ) : (
          <div className="grid cols-3 tight">
            <Field label="Entity type"><input value={form.entityType} onChange={(e) => set("entityType", e.target.value)} /></Field>
            <Field label="Vendor"><input value={form.vendorName} onChange={(e) => set("vendorName", e.target.value)} /></Field>
            <Field label="Region"><input value={form.region} onChange={(e) => set("region", e.target.value)} /></Field>
            <Field label="Tenant"><input value={form.tenant} onChange={(e) => set("tenant", e.target.value)} /></Field>
          </div>
        )}
        <div className="grid cols-3 tight">
          <Field label="Wave size" hint="Elements per wave; blank: all in one wave"><input inputMode="numeric" value={form.waveSize} onChange={(e) => set("waveSize", e.target.value)} /></Field>
          <Field label="Pause between waves, seconds"><input inputMode="numeric" value={form.wavePauseSeconds} onChange={(e) => set("wavePauseSeconds", e.target.value)} /></Field>
          <Field label="New critical/major alarms allowed" hint="More than this on a wave's elements fails its gate"><input inputMode="numeric" value={form.gateMaxNewAlarms} onChange={(e) => set("gateMaxNewAlarms", e.target.value)} /></Field>
          <Field label="Job timeout, seconds" hint="Blank: wait for every job however long. A job still running then is failed, and the gate sees it"><input inputMode="numeric" value={form.jobTimeoutSeconds} onChange={(e) => set("jobTimeoutSeconds", e.target.value)} /></Field>
        </div>
        <div className="grid cols-3 tight">
          <Field label="If a gate fails">
            <select value={form.onGateFailure} onChange={(e) => set("onGateFailure", e.target.value as "halt" | "rollback")}>
              <option value="halt">Halt (an operator decides)</option><option value="rollback">Roll back what was done</option>
            </select>
          </Field>
          <Field label="A rollback undoes">
            <select value={form.rollbackOrder} onChange={(e) => set("rollbackOrder", e.target.value as "all" | "reverse")}>
              <option value="reverse">The last wave first, then each earlier wave</option><option value="all">Every wave at once</option>
            </select>
          </Field>
        </div>
        {preview && (
          <div role="status" className="small" aria-label="Waves">
            <strong>{preview.waveCount} wave(s)</strong>
            {preview.waves.map((w, i) => <div key={i}>Wave {i + 1}: <code>{w.join(", ")}</code></div>)}
          </div>
        )}
        {problem && <div className="error-box" role="alert">{problem}</div>}
        <div className="row gap end">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="button" className="btn" disabled={action.isPending} onClick={() => run(true)}>Preview waves</button>
          <button className="btn primary" disabled={action.isPending} onClick={() => run(false)}>{action.isPending ? "…" : "Start"}</button>
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------- detail

/** One campaign: its state and why it is held, the actions its state allows, the waves with each element's job, what needs attention, and the event log. */
export function CampaignDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const report = useSmo<CampaignReport>(`${CAMPAIGNS}/${id}/report`, undefined, { refetchInterval: 5_000 });
  const { can } = useAuth();
  const data = report.data;
  return (
    <Drawer title={<>Campaign {data ? data.name : <Id value={id} />}</>} onClose={onClose}>
      <ErrorBox error={report.error} />
      {data && <>
        <div className="row gap wrap">
          <StateBadge state={data.status} />
          <span className="muted small">{CAMPAIGN_MEANING[data.status]}</span>
        </div>
        {data.haltedReason && <p className="small" role="status"><strong>{describeHalt(data.haltedReason)}.</strong> {data.haltedDetail ?? ""}{data.nextWaveAt ? ` Continues by itself at ${formatTime(data.nextWaveAt)}.` : ""}</p>}
        {(can("POST", `${CAMPAIGNS}/${id}/halt`)) && <CampaignActions campaign={data} onDone={() => report.refetch()} />}
        <KeyValue items={[
          ["Campaign", <Id key="i" value={data.campaignId} />], ["Started by", data.requestedBy], ["Software", data.softwareVersion], ["Elements", `${data.summary.elements} (${describeSelector(data.selector)})`],
          ["Progress", waveProgress(data)], ["Settings", <span key="s" className="small">{describeSettings(data)}</span>],
          ["Started", formatTime(data.createdAt)], ["Finished", formatTime(data.finishedAt)],
        ]} />
        <Card title="Totals">
          <KeyValue items={[
            ["Started", `${data.summary.started} of ${data.summary.elements}`], ["Not reached", data.summary.notReached], ["Completed", data.summary.completed],
            ["Failed", data.summary.failed], ["Running", data.summary.inProgress], ["Reverted", data.summary.reverted],
          ]} />
        </Card>
        {data.attention.length > 0 && (
          <Card title={`Needs attention (${data.attention.length})`}>
            <ul aria-label="Needs attention">{data.attention.map((a, i) => <li key={i} className="small"><strong>{a.managedElementRef}</strong>: {a.problem}</li>)}</ul>
          </Card>
        )}
        {data.waves.map((w) => (
          <Card key={w.wave} title={`Wave ${w.wave}${w.started ? "" : " (not started)"}`}>
            <DataTable rows={w.started ? w.jobs : w.elements.map((e) => ({ managedElementRef: e, jobId: "", phase: "—", status: "", revert: null }))} rowKey={(j) => `${j.managedElementRef}-${j.jobId}`} empty="No jobs." columns={[
              { header: "Element", render: (j) => j.managedElementRef },
              { header: "Phase", render: (j) => j.phase },
              { header: "Job", render: (j) => (j.status ? <><StateBadge state={j.status} />{j.timedOut && <span className="badge tone-bad" title="The element did not report in time"> timed out</span>}</> : <span className="muted">waiting</span>) },
              { header: "Revert", render: (j) => (j.revert ? <StateBadge state={j.revert} /> : <span className="muted">—</span>) },
            ]} />
          </Card>
        ))}
        <Card title="Events">
          <ol aria-label="Campaign events" className="small">
            {data.events.map((e, i) => <li key={i}><span className="muted">{formatTime(e.at)}</span> <strong>{e.event}</strong>{e.wave ? ` (wave ${e.wave})` : ""}{e.detail ? `: ${e.detail}` : ""}{e.by ? ` — ${e.by}` : ""}</li>)}
          </ol>
        </Card>
      </>}
    </Drawer>
  );
}

function CampaignActions({ campaign, onDone }: { campaign: CampaignReport; onDone: () => void }) {
  const allowed = campaignActions(campaign.status, campaign.haltedReason);
  const base = `${CAMPAIGNS}/${campaign.campaignId}`;
  const [force, setForce] = useState(false);
  if (!allowed.halt && !allowed.continue && !allowed.abort && !allowed.rollback) return null;
  return (
    <div className="row gap wrap" role="group" aria-label="Campaign actions">
      {allowed.continue && <>
        <ActionButton label="Continue" tone="primary" onDone={onDone} title="Go on with the next wave (after a failed gate: anyway)"
          confirm={campaign.haltedReason === "GATE_FAILED" ? "The gate of this wave failed. Continue to the next wave anyway?" : undefined}
          action={{ method: "POST", path: `${base}/continue`, json: allowed.canForce && force ? { force: true } : {}, success: "Campaign continues" }} />
        {allowed.canForce && <label className="row gap small"><input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} /> Do not wait for the pause</label>}
      </>}
      {allowed.halt && <ActionButton label="Halt" onDone={onDone} title="Stop the next wave from starting; jobs already running go on"
        action={{ method: "POST", path: `${base}/halt`, json: {}, success: "Campaign halted" }} />}
      {allowed.abort && <ActionButton label="Abort" tone="danger" onDone={onDone} title="End the campaign here; the waves that ran stay as they are"
        confirm="Abort this campaign? The waves that did not run never will; the ones that ran stay (roll back to undo them)."
        action={{ method: "POST", path: `${base}/abort`, json: {}, success: "Campaign aborted" }} />}
      {allowed.rollback && <ActionButton label="Roll back" tone="danger" onDone={onDone} title="Start a revert job for each completed job"
        confirm={`Roll back this campaign? A revert job is started for every job that completed${campaign.rollbackOrder === "reverse" ? ", the last wave first" : ", all at once"}. It is refused while a job of the campaign is still running.`}
        action={{ method: "POST", path: `${base}/rollback`, json: {}, success: "Rollback started" }} />}
    </div>
  );
}
