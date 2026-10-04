import { useState } from "react";

import { useSmo, useSmoAction } from "../api/hooks";
import type { InstanceSafeguards, InstanceSummary, RappKill, RefusalCode, SafeguardRefusal, SafeguardSubscription } from "../api/types";
import { ActionButton, Can, Card, DataTable, Field, Id, Modal, PageHeader, StateBadge, Tabs, useHashTab } from "../components/ui";
import { REFUSAL_CODES, REFUSAL_MEANING, describeLimits, formatTime, limitsForm, limitsPayload } from "../lib/domain";

const TABS = ["rapps", "refusals", "watchers"] as const;

export function Safeguards() {
  const [tab, setTab] = useHashTab(TABS, "rapps");
  return (
    <>
      <PageHeader title="Safeguards" subtitle="What holds an rApp in check: stop it, set how much it may change, and see every time it was refused" />
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: "rapps", label: "rApp limits & stop" }, { id: "refusals", label: "Refusals" }, { id: "watchers", label: "Watchers" },
      ]} />
      {tab === "rapps" && <RappLimits />}
      {tab === "refusals" && <Refusals />}
      {tab === "watchers" && <Watchers />}
    </>
  );
}

// ---------------------------------------------------------------- rApp limits and the stop switch

function RappLimits() {
  const instances = useSmo<InstanceSummary[]>("/rapp-mgmt/instances", { limit: 200 });
  return (
    <>
      <Card title="rApp instances">
        <p className="muted small">
          A stopped rApp's config jobs are refused until an admin resumes it; undoing changes (rollback, revert, halt, abort) always works.
          Limits cap how many config jobs it may start per hour, how many managed elements one job may touch, and how far a value may move in one write.
        </p>
        <div className="table-wrap">
          <table className="table">
            <thead><tr><th>Instance</th><th>Autonomy</th><th>Status</th><th>Limits</th><th /></tr></thead>
            <tbody>
              {instances.error ? <tr><td colSpan={5} className="muted center">{String(instances.error)}</td></tr>
                : instances.data?.length === 0 ? <tr><td colSpan={5} className="muted center">No rApp instances yet.</td></tr>
                : instances.data?.map((i) => <InstanceRow key={i.instanceId} instance={i} />)}
            </tbody>
          </table>
        </div>
      </Card>
      <Stopped />
    </>
  );
}

function InstanceRow({ instance }: { instance: InstanceSummary }) {
  const sg = useSmo<InstanceSafeguards>(`/rapp-mgmt/instances/${instance.instanceId}/safeguards`);
  const [dialog, setDialog] = useState<"stop" | "limits" | null>(null);
  const view = sg.data;
  const invoker = view?.invokerId ?? null;
  return (
    <tr>
      <td><Id value={instance.instanceId} /> <StateBadge state={instance.state} /></td>
      <td><StateBadge state={instance.autonomyMode} /></td>
      <td>
        {sg.error ? <span className="muted" title={String(sg.error)}>unknown</span>
          : !view ? <span className="muted">…</span>
          : !invoker ? <span className="muted" title="A terminated instance has no credential, so nothing to stop or limit">no credential</span>
          : view.killed ? <span title={`${view.kill?.killedBy ?? ""}: ${view.kill?.reason ?? "no reason given"} (${formatTime(view.kill?.killedAt)})`}><StateBadge state="DISABLED" /> stopped</span>
          : <StateBadge state="ACTIVE" />}
      </td>
      <td>{view ? (invoker ? describeLimits(view.limits) : <span className="muted">—</span>) : "…"}</td>
      <td className="row gap">
        {invoker && view && <>
          {view.killed
            ? <ActionButton action={{ method: "DELETE", path: `/rapp-mgmt/instances/${instance.instanceId}/kill`, success: "rApp may write again" }}
                label="Resume" confirm="Let this rApp start config jobs again?" />
            : <Can method="PUT" path={`/rapp-mgmt/instances/${instance.instanceId}/kill`}>
                <button className="btn danger" onClick={() => setDialog("stop")}>Stop</button>
              </Can>}
          <Can method="PUT" path={`/ran-nf-oam/rapp-limits/${invoker}`}>
            <button className="btn" onClick={() => setDialog("limits")}>Limits…</button>
          </Can>
          {view.limits && <ActionButton action={{ method: "DELETE", path: `/ran-nf-oam/rapp-limits/${invoker}`, success: "Limits removed" }}
            label="Remove limits" tone="danger" confirm="Remove every limit of this rApp?" />}
        </>}
      </td>
      {dialog === "stop" && <StopDialog instanceId={instance.instanceId} onClose={() => setDialog(null)} />}
      {dialog === "limits" && invoker && <LimitsDialog invokerId={invoker} current={view?.limits ?? null} onClose={() => setDialog(null)} />}
    </tr>
  );
}

function StopDialog({ instanceId, onClose }: { instanceId: string; onClose: () => void }) {
  const [reason, setReason] = useState("");
  const action = useSmoAction();
  return (
    <Modal title="Stop this rApp" onClose={onClose}>
      <p className="muted small">Its config jobs (dry runs too) are refused at once, and every other change it makes through the gateway is refused within a few seconds. It can still read, withdraw what it made (delete) and undo its own config jobs. The instance itself keeps running.</p>
      <Field label="Reason" hint="Shown to everyone who looks, and in the refusal events">
        <input value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} autoFocus />
      </Field>
      <div className="row gap">
        <button className="btn danger" disabled={action.isPending} onClick={() => action.mutate(
          { method: "PUT", path: `/rapp-mgmt/instances/${instanceId}/kill`, json: { reason: reason.trim() || null }, success: "rApp stopped" }, { onSuccess: onClose })}>
          {action.isPending ? "…" : "Stop rApp"}
        </button>
        <button className="btn ghost" onClick={onClose}>Cancel</button>
      </div>
    </Modal>
  );
}

function LimitsDialog({ invokerId, current, onClose }: { invokerId: string; current: InstanceSafeguards["limits"]; onClose: () => void }) {
  const [form, setForm] = useState(limitsForm(current));
  const [problem, setProblem] = useState<string | null>(null);
  const action = useSmoAction();
  const save = () => {
    const parsed = limitsPayload(form);
    if (!parsed.ok) return setProblem(parsed.error);
    setProblem(null);
    action.mutate({ method: "PUT", path: `/ran-nf-oam/rapp-limits/${invokerId}`, json: parsed.body, success: "Limits saved" }, { onSuccess: onClose });
  };
  return (
    <Modal title="Limits for this rApp" onClose={onClose}>
      <p className="muted small">Saving replaces the whole set: a blank field removes that limit. Rollbacks and reverts are never limited.</p>
      <Field label="Config jobs per hour" hint="1 to 100000"><input inputMode="numeric" value={form.jobsPerHour} onChange={(e) => setForm({ ...form, jobsPerHour: e.target.value })} /></Field>
      <Field label="Managed elements per job" hint="1 to 10000: how many elements one job may touch"><input inputMode="numeric" value={form.elementsPerJob} onChange={(e) => setForm({ ...form, elementsPerJob: e.target.value })} /></Field>
      <Field label="Change per write, %" hint="Greater than 0, up to 10000: how far a numeric value may move, in percent of its current value">
        <input inputMode="decimal" value={form.changePercent} onChange={(e) => setForm({ ...form, changePercent: e.target.value })} />
      </Field>
      {problem && <div className="error-box" role="alert">{problem}</div>}
      <div className="row gap">
        <button className="btn primary" disabled={action.isPending} onClick={save}>{action.isPending ? "…" : "Save limits"}</button>
        <button className="btn ghost" onClick={onClose}>Cancel</button>
      </div>
    </Modal>
  );
}

function Stopped() {
  const stopped = useSmo<RappKill[]>("/ran-nf-oam/rapp-kill", { limit: 200 });
  return (
    <Card title="Stopped rApps">
      <p className="muted small">Everything stopped at RAN NF OAM, by invoker id, including rApps that are not instances of this platform. Resume an instance from the table above.</p>
      <DataTable rows={stopped.data} rowKey={(r) => r.invokerId} error={stopped.error} loading={stopped.isLoading} empty="No rApp is stopped." columns={[
        { header: "Invoker", render: (r) => <Id value={r.invokerId} /> },
        { header: "Stopped by", render: (r) => r.killedBy },
        { header: "Reason", render: (r) => r.reason ?? <span className="muted">—</span> },
        { header: "Since", render: (r) => formatTime(r.killedAt) },
      ]} />
    </Card>
  );
}

// ---------------------------------------------------------------- refusals

function Refusals() {
  const [code, setCode] = useState<RefusalCode | "">("");
  const [invoker, setInvoker] = useState("");
  const refusals = useSmo<SafeguardRefusal[]>("/ran-nf-oam/safeguard-refusals", { limit: 100, code: code || undefined, invoker_id: invoker.trim() || undefined });
  return (
    <Card title="Refusals, newest first" actions={<>
      <select value={code} onChange={(e) => setCode(e.target.value as RefusalCode | "")} aria-label="Reason">
        <option value="">Any reason</option>
        {REFUSAL_CODES.map((c) => <option key={c} value={c}>{c}</option>)}
      </select>
      <input placeholder="rApp invoker id" value={invoker} onChange={(e) => setInvoker(e.target.value)} aria-label="Invoker id" />
    </>}>
      <p className="muted small">Every time the platform refused an rApp, whether or not anyone was watching. Repeats of the same refusal are all recorded here but announced to watchers once a minute.</p>
      <DataTable rows={refusals.data} rowKey={(r) => r.refusalId} error={refusals.error} loading={refusals.isLoading} empty="No refusals recorded." columns={[
        { header: "When", render: (r) => formatTime(r.occurredAt) },
        { header: "rApp", render: (r) => <Id value={r.invokerId} /> },
        { header: "Refused", render: (r) => <span title={REFUSAL_MEANING[r.refusal]}><StateBadge state="REJECTED" /> {r.refusal}</span> },
        { header: "Requested by", render: (r) => r.requestedBy ?? <span className="muted">—</span> },
        { header: "Detail", render: (r) => r.detail ?? <span className="muted">—</span> },
        { header: "Announced", render: (r) => (r.announced ? "yes" : <span className="muted">no</span>) },
      ]} />
    </Card>
  );
}

// ---------------------------------------------------------------- watchers (subscriptions)

function Watchers() {
  const subs = useSmo<SafeguardSubscription[]>("/ran-nf-oam/safeguard-subscriptions", { limit: 200 });
  const [adding, setAdding] = useState(false);
  return (
    <Card title="Who is told about refusals" actions={
      <Can method="POST" path="/ran-nf-oam/safeguard-subscriptions"><button className="btn primary" onClick={() => setAdding(true)}>Add watcher</button></Can>}>
      <p className="muted small">Each refusal is posted to every watcher (a webhook), through the delivery queue, so a watcher that is down misses nothing for long.</p>
      <DataTable rows={subs.data} rowKey={(s) => s.subscriptionId} error={subs.error} loading={subs.isLoading} empty="Nobody is watching." columns={[
        { header: "Callback", render: (s) => <code className="small">{s.callbackUri}</code> },
        { header: "Refusals", render: (s) => (s.refusals.length ? s.refusals.join(", ") : "all") },
        { header: "Since", render: (s) => formatTime(s.createdAt) },
        { header: "", render: (s) => <ActionButton action={{ method: "DELETE", path: `/ran-nf-oam/safeguard-subscriptions/${s.subscriptionId}`, success: "Watcher removed" }}
            label="Remove" tone="danger" confirm="Stop telling this watcher?" /> },
      ]} />
      {adding && <AddWatcher onClose={() => setAdding(false)} />}
    </Card>
  );
}

function AddWatcher({ onClose }: { onClose: () => void }) {
  const [uri, setUri] = useState("");
  const [codes, setCodes] = useState<RefusalCode[]>([]);
  const action = useSmoAction();
  const toggle = (c: RefusalCode) => setCodes((cur) => (cur.includes(c) ? cur.filter((x) => x !== c) : [...cur, c]));
  return (
    <Modal title="Add a watcher" onClose={onClose}>
      <Field label="Callback URL" hint="Refused if it points at a private or internal address"><input value={uri} onChange={(e) => setUri(e.target.value)} placeholder="https://" autoFocus /></Field>
      <fieldset className="field">
        <legend className="field-label">Which refusals (none ticked: all)</legend>
        {REFUSAL_CODES.map((c) => (
          <label key={c} className="row gap small"><input type="checkbox" checked={codes.includes(c)} onChange={() => toggle(c)} /> {c} <span className="muted">{REFUSAL_MEANING[c]}</span></label>
        ))}
      </fieldset>
      <div className="row gap">
        <button className="btn primary" disabled={action.isPending || !uri.trim()} onClick={() => action.mutate(
          { method: "POST", path: "/ran-nf-oam/safeguard-subscriptions", json: { callbackUri: uri.trim(), refusals: codes }, success: "Watcher added" }, { onSuccess: onClose })}>
          {action.isPending ? "…" : "Add watcher"}
        </button>
        <button className="btn ghost" onClick={onClose}>Cancel</button>
      </div>
    </Modal>
  );
}
