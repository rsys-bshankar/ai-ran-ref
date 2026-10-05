import { useState } from "react";

import { useSmo } from "../api/hooks";
import type { AutonomyDispatch, InstanceSummary, Intent, IntentReport, Rmih } from "../api/types";
import { ActionButton, Can, Card, DataTable, Drawer, Field, Id, Json, KeyValue, PageHeader, StateBadge, Tabs, useHashTab } from "../components/ui";
import { formatTime, parseJsonObject, splitList } from "../lib/domain";

const TABS = ["intents", "handlers", "autonomy"] as const;

export function Policy() {
  const [tab, setTab] = useHashTab(TABS, "intents");
  return (
    <>
      <PageHeader title="Intents" subtitle="TS 28.312 intents dispatched to intent handlers" />
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: "intents", label: "Intents" }, { id: "handlers", label: "Intent handlers (RMIH)" }, { id: "autonomy", label: "Autonomy dispatches" },
      ]} />
      {tab === "intents" && <Intents />}
      {tab === "handlers" && <Handlers />}
      {tab === "autonomy" && <AutonomyDispatches />}
    </>
  );
}

// ---------------------------------------------------------------- intents

const PURPOSES = ["FULFILMENT_WITHOUT_NEGOTIATION", "FULFILMENT_WITH_NEGOTIATION", "FEASIBILITYCHECK", "FEASIBILITYCHECK_WITH_RECOMMENDATIONS", "EXPLORATION"];
const OBJECT_TYPES = ["RAN_SUBNETWORK", "EDGE_SERVICE_SUPPORT", "5GC_SUBNETWORK", "RADIO_SERVICE", "SUBNETWORK"];

function Intents() {
  const [state, setState] = useState("");
  const intents = useSmo<Intent[]>("/intent-service/intents", { admin_state: state });
  const [selected, setSelected] = useState<Intent | null>(null);
  return (
    <>
      <Can method="POST" path="/intent-service/intents"><CreateIntent /></Can>
      <Card title="Intents" actions={<select value={state} onChange={(e) => setState(e.target.value)} aria-label="Admin state"><option value="">All</option><option>ACTIVATED</option><option>DEACTIVATED</option></select>}>
        <p className="muted small">Intents created here carry RMIO identity <code>smo-gui</code>; only an intent's creator may change its admin state.</p>
        <DataTable rows={intents.data} loading={intents.isLoading} error={intents.error} rowKey={(i) => i.intentId} empty="No intents."
          onRowClick={setSelected} selectedKey={selected?.intentId} columns={[
            { header: "Intent", render: (i) => <Id value={i.intentId} /> },
            { header: "RMIO", render: (i) => i.rmioId || <span className="muted">—</span> },
            { header: "Priority", render: (i) => i.intentPriority },
            { header: "Purpose", render: (i) => <span className="small">{i.intentMgmtPurpose}</span> },
            { header: "Admin state", render: (i) => <StateBadge state={i.intentAdminState} /> },
            { header: "", className: "actions", render: (i) => <IntentActions intent={i} /> },
          ]} />
      </Card>
      {selected && <IntentDrawer intent={selected} onClose={() => setSelected(null)} />}
    </>
  );
}

function IntentActions({ intent }: { intent: Intent }) {
  const path = `/intent-service/intents/${intent.intentId}`;
  const next = intent.intentAdminState === "ACTIVATED" ? "DEACTIVATED" : "ACTIVATED";
  return (
    <div className="row gap end">
      {intent.rmioId === "smo-gui" && <ActionButton label={next === "ACTIVATED" ? "Activate" : "Deactivate"} action={{ method: "PATCH", path: `${path}/admin-state`, json: { newState: next }, success: `Intent ${next}` }} />}
      <ActionButton label="Delete" tone="danger" confirm="Delete (retract) this intent?" action={{ method: "DELETE", path, success: "Intent deleted" }} />
    </div>
  );
}

/** A spec-valid default target per expectation family (TS 28.312: each family fixes its targets' conditions/value ranges). */
const DEFAULT_TARGETS: Record<string, string> = {
  RAN_SUBNETWORK: '[{"targetName": "RANEnergyConsumption", "targetCondition": "IS_LESS_THAN", "targetValueRange": 500}]',
  RADIO_SERVICE: '[{"targetName": "DlThptPerUE", "targetCondition": "IS_GREATER_THAN", "targetValueRange": 50}]',
  "5GC_SUBNETWORK": '[{"targetName": "Latency", "targetCondition": "IS_LESS_THAN", "targetValueRange": 20}]',
  EDGE_SERVICE_SUPPORT: '[{"targetName": "DlLatency", "targetCondition": "IS_LESS_THAN", "targetValueRange": 20}]',
  SUBNETWORK: '[{"targetName": "MaintenanceVersion", "targetCondition": "IS_EQUAL_TO", "targetValueRange": "1.0"}]',
};

/** One TS 28.312 IntentExpectation from the form's object type + targets. */
function expectationOf(objectType: string, targets: unknown[]) {
  return { expectationId: "e1", expectationVerb: "DELIVER", expectationObject: { objectType }, expectationTargets: targets };
}

function CreateIntent() {
  const handlers = useSmo<Rmih[]>("/intent-service/intent-handling-functions");
  const [rmihId, setRmihId] = useState("");
  const [userLabel, setUserLabel] = useState("");
  const [objectType, setObjectType] = useState("RAN_SUBNETWORK");
  const [targets, setTargets] = useState(DEFAULT_TARGETS.RAN_SUBNETWORK);
  const [priority, setPriority] = useState("1");
  const [reportTo, setReportTo] = useState("");
  const [purpose, setPurpose] = useState(PURPOSES[0]);
  const [scope, setScope] = useState("");
  let parsedTargets: unknown[] | null = null;
  try { const v = JSON.parse(targets); parsedTargets = Array.isArray(v) ? v : null; } catch { parsedTargets = null; }
  return (
    <Card title="Create intent">
      <p className="muted small">A strict TS 28.312 Intent, addressed to one handler you choose below (consumer-side selection). Rejected at creation if that handler's declared capabilities, targets or scope don't cover it; a feasibility-check purpose is accepted and reports what isn't feasible.</p>
      <div className="form grid cols-3 tight">
        <Field label="Handler (RMIH)"><select value={rmihId} onChange={(e) => setRmihId(e.target.value)}><option value="">select…</option>{(handlers.data ?? []).map((h) => <option key={h.rmihId} value={h.rmihId}>{h.rmihId}</option>)}</select></Field>
        <Field label="User label"><input value={userLabel} onChange={(e) => setUserLabel(e.target.value)} placeholder="e.g. night-time energy saving" /></Field>
        <Field label="Expectation object type"><select value={objectType} onChange={(e) => { setObjectType(e.target.value); setTargets(DEFAULT_TARGETS[e.target.value] ?? "[]"); }}>{OBJECT_TYPES.map((t) => <option key={t}>{t}</option>)}</select></Field>
        <Field label="Report recipient" hint="Optional — receives this Intent's reports (intentReportControl)."><input value={reportTo} onChange={(e) => setReportTo(e.target.value)} placeholder="http://rapp:8000/intent-reports" /></Field>
        <Field label="Priority"><input type="number" min={1} value={priority} onChange={(e) => setPriority(e.target.value)} /></Field>
        <Field label="Handling scope"><select value={scope} onChange={(e) => setScope(e.target.value)}><option value="">any</option><option>RAN</option><option>CN</option></select></Field>
        <Field label="Purpose"><select value={purpose} onChange={(e) => setPurpose(e.target.value)}>{PURPOSES.map((p) => <option key={p}>{p}</option>)}</select></Field>
        <Field label="Expectation targets (JSON array)" hint={parsedTargets ? undefined : <span className="text-bad">must be a JSON array</span>}><textarea rows={2} value={targets} onChange={(e) => setTargets(e.target.value)} spellCheck={false} /></Field>
      </div>
      <ActionButton label="Create intent" tone="primary" disabled={!parsedTargets || parsedTargets.length === 0 || !rmihId || !userLabel} action={{
        method: "POST", path: "/intent-service/intents", success: "Intent created",
        json: {
          rmihId, userLabel,
          intentExpectations: [expectationOf(objectType, parsedTargets ?? [])],
          intentReportControl: [reportTo ? { observationPeriod: 60, reportRecipientAddress: reportTo } : { observationPeriod: 60 }],
          intentPriority: Number(priority) || 1, intentMgmtPurpose: purpose, intentHandlingScope: scope || null,
        },
      }} />
    </Card>
  );
}

function IntentDrawer({ intent, onClose }: { intent: Intent; onClose: () => void }) {
  const reports = useSmo<IntentReport[]>("/intent-service/intent-reports", { intent_id: intent.intentId });
  return (
    <Drawer title={<>Intent <Id value={intent.intentId} /></>} onClose={onClose}>
      <div className="row between"><StateBadge state={intent.intentAdminState} /><IntentActions intent={intent} /></div>
      <KeyValue items={[["Intent ID", <code>{intent.intentId}</code>], ["Label", intent.userLabel ?? "—"], ["RMIO", intent.rmioId], ["Priority", intent.intentPriority], ["Purpose", intent.intentMgmtPurpose]]} />
      <h3>Expectations</h3>
      <Json value={intent.attributes?.intentExpectations ?? []} />
      <h3>Intent reports</h3>
      <Can method="POST" path="/intent-service/intent-reports"><PublishIntentReport intentId={intent.intentId} /></Can>
      {(reports.data ?? []).length === 0 ? <p className="muted">No reports published by a handler yet.</p> : reports.data!.map((r) => (
        <div key={r.reportId} className="report">
          <div className="muted small">{formatTime(r.attributes.lastUpdatedTime)}</div>
          <Json value={r.attributes} />
        </div>
      ))}
    </Drawer>
  );
}

function Handlers() {
  const handlers = useSmo<Rmih[]>("/intent-service/intent-handling-functions");
  const [f, setF] = useState({ rmihId: "so-smos", smeServiceId: "so-smos-intent-handler", callback: "http://so-smos:8000/intents", types: "RAN_SUBNETWORK", targetNames: "RANEnergyConsumption", scope: "RAN" });
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const uuidLike = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(f.rmihId);
  return (
    <Card title="Registered intent handlers" actions={<span className="muted small">Framework-internal only (SO SMOS / SA SMOS) — D-SEC-POLICY-1</span>}>
      <DataTable rows={handlers.data} loading={handlers.isLoading} error={handlers.error} rowKey={(h) => h.rmihId} empty="No intent handlers registered." columns={[
        { header: "RMIH", render: (h) => <strong>{h.rmihId}</strong> },
        { header: "Supported object types", render: (h) => (h.attributes?.intentHandlingCapabilityList ?? []).map((c) => c.supportedExpectationObjectType).join(", ") },
        { header: "Supported targets", render: (h) => [...new Set((h.attributes?.intentHandlingCapabilityList ?? []).flatMap((c) => c.supportedExpectationTargetInfoList.map((t) => t.supportedTargetName)))].join(", ") || "—" },
        { header: "Scope", render: (h) => h.intentHandlingScope?.join(", ") ?? "any" },
        { header: "Callback", render: (h) => <code className="small">{h.notificationDestination}</code> },
        { header: "", className: "actions", render: (h) => <ActionButton label="Deregister" tone="danger" confirm={`Deregister ${h.rmihId}?`}
          action={{ method: "DELETE", path: `/intent-service/intent-handling-functions/${h.rmihId}`, success: "Handler deregistered" }} /> },
      ]} />
      <Can method="POST" path="/intent-service/intent-handling-functions">
        <details className="admin-tools">
          <summary>Admin: register a handler on the framework's behalf</summary>
          <p className="muted small">An rApp identity (a UUID) is always refused: only SMO modules such as <code>so-smos</code> / <code>sa-smos</code> may hold an rmihId.</p>
          <div className="form grid cols-3 tight">
            <Field label="RMIH ID" hint={uuidLike ? <span className="text-bad">an rApp id — Intent Service will refuse it</span> : undefined}><input value={f.rmihId} onChange={set("rmihId")} /></Field>
            <Field label="SME service ID"><input value={f.smeServiceId} onChange={set("smeServiceId")} /></Field>
            <Field label="Notification callback"><input value={f.callback} onChange={set("callback")} /></Field>
            <Field label="Supported expectation object types" hint="Comma-separated: RAN_SUBNETWORK, EDGE_SERVICE_SUPPORT, 5GC_SUBNETWORK, RADIO_SERVICE"><input value={f.types} onChange={set("types")} /></Field>
            <Field label="Supported target names" hint="Comma-separated, e.g. RANEnergyConsumption, AveDLPrbLoad — an Intent's targets must be among them"><input value={f.targetNames} onChange={set("targetNames")} /></Field>
            <Field label="Handling scope"><select value={f.scope} onChange={set("scope")}><option value="">any</option><option>RAN</option><option>CN</option></select></Field>
          </div>
          <ActionButton label="Register handler" disabled={!f.rmihId || !f.types} action={{
            method: "POST", path: "/intent-service/intent-handling-functions", success: "Handler registered",
            json: { rmihId: f.rmihId, smeServiceId: f.smeServiceId, notificationDestination: f.callback,
              intentHandlingCapabilityList: splitList(f.types).map((t) => ({
                intentHandlingCapabilityId: `cap-${t}`, supportedExpectationObjectType: t,
                supportedExpectationTargetInfoList: splitList(f.targetNames).map((n) => ({ supportedTargetName: n })),
              })),
              intentHandlingScope: f.scope ? [f.scope] : null },
          }} />
        </details>
      </Can>
    </Card>
  );
}

function PublishIntentReport({ intentId }: { intentId: string }) {
  const [status, setStatus] = useState("FULFILLED");
  const [conflicts, setConflicts] = useState("");
  return (
    <details className="admin-tools">
      <summary>Admin: publish a report as the handling RMIH</summary>
      <div className="form inline">
        <Field label="Fulfilment" hint="FULFILLED, or NOT_FULFILLED with its TS 28.312 state"><select value={status} onChange={(e) => setStatus(e.target.value)}>{["FULFILLED", "RECEIVED", "DEGRADED", "SUSPENDED", "TERMINATED"].map((s) => <option key={s}>{s}</option>)}</select></Field>
        <Field label="Conflicting intents" hint="Comma-separated intent ids"><input value={conflicts} onChange={(e) => setConflicts(e.target.value)} /></Field>
      </div>
      <ActionButton label="Publish report" action={{
        method: "POST", path: "/intent-service/intent-reports", success: "Report published",
        json: {
          intentReference: intentId,
          intentFulfilmentReport: { intentFulfilmentInfo: status === "FULFILLED" ? { fulfilmentStatus: "FULFILLED" } : { fulfilmentStatus: "NOT_FULFILLED", notFullfilledState: status } },
          intentConflictReports: splitList(conflicts).length
            ? splitList(conflicts).map((c, i) => ({ conflictId: `c${i + 1}`, conflictType: "INTENT_CONFLICT", conflictingIntent: c })) : null,
        },
      }} />
    </details>
  );
}

// ---------------------------------------------------------------- HISTORY.md OI-6.3: rApp Autonomy Modes

function AutonomyDispatches() {
  const [status, setStatus] = useState("");
  const dispatches = useSmo<AutonomyDispatch[]>("/intent-service/autonomy-dispatches", { status });
  return (
    <>
      <Can method="POST" path="/intent-service/autonomy-dispatches"><CreateAutonomyDispatch /></Can>
      <Card title="Autonomy dispatches" actions={
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter by status">
          <option value="">All</option><option>AWAITING_SCOPE</option><option>DISPATCHED</option><option>SHADOWED</option><option>REJECTED</option>
        </select>}>
        <p className="muted small">What an rApp instance's own onboarding-time autonomy mode did with an inference outcome — AUTONOMOUS dispatches immediately at a pre-configured scope, ASSIST waits in AWAITING_SCOPE until an operator either scopes it (an Intent is created) or rejects it (nothing is dispatched), SHADOW never dispatches at all.</p>
        <DataTable rows={dispatches.data} loading={dispatches.isLoading} error={dispatches.error} rowKey={(d) => d.dispatchId} empty="No autonomy dispatches." columns={[
          { header: "Dispatch", render: (d) => <Id value={d.dispatchId} /> },
          { header: "Instance", render: (d) => <Id value={d.instanceId} /> },
          { header: "Mode", render: (d) => <StateBadge state={d.autonomyMode} /> },
          { header: "Status", render: (d) => <StateBadge state={d.status} /> },
          { header: "Intent", render: (d) => d.intentId ? <Id value={d.intentId} /> : <span className="muted">—</span> },
          { header: "Rejected", render: (d) => d.rejectedBy ? <span className="small">{d.rejectedBy}{d.rejectionReason ? ` — ${d.rejectionReason}` : ""}</span> : <span className="muted">—</span> },
          { header: "", className: "actions", render: (d) => d.status === "AWAITING_SCOPE" && <ResolveAutonomyDispatch dispatch={d} /> },
        ]} />
      </Card>
    </>
  );
}

function CreateAutonomyDispatch() {
  const instances = useSmo<InstanceSummary[]>("/rapp-mgmt/instances");
  const handlers = useSmo<Rmih[]>("/intent-service/intent-handling-functions");
  const [instanceId, setInstanceId] = useState("");
  const [rmihId, setRmihId] = useState("");
  const [objectType, setObjectType] = useState("RAN_SUBNETWORK");
  const [targets, setTargets] = useState(DEFAULT_TARGETS.RAN_SUBNETWORK);
  const [notificationDestination, setNotificationDestination] = useState("");
  let parsedTargets: unknown[] | null = null;
  try { const v = JSON.parse(targets); parsedTargets = Array.isArray(v) ? v : null; } catch { parsedTargets = null; }
  const selected = instances.data?.find((i) => i.instanceId === instanceId);
  return (
    <Card title="Request autonomy dispatch">
      <p className="muted small">Simulates an rApp instance that just pulled an AI/ML inference outcome and wants it enacted — the selected instance's own <code>autonomyMode</code> decides what happens next.</p>
      <div className="form grid cols-3 tight">
        <Field label="rApp instance">
          <select value={instanceId} onChange={(e) => setInstanceId(e.target.value)}>
            <option value="">select…</option>
            {(instances.data ?? []).map((i) => <option key={i.instanceId} value={i.instanceId}>{i.instanceId} ({i.autonomyMode})</option>)}
          </select>
        </Field>
        <Field label="Handler (RMIH)"><select value={rmihId} onChange={(e) => setRmihId(e.target.value)}><option value="">select…</option>{(handlers.data ?? []).map((h) => <option key={h.rmihId} value={h.rmihId}>{h.rmihId}</option>)}</select></Field>
        <Field label="Expectation object type"><select value={objectType} onChange={(e) => { setObjectType(e.target.value); setTargets(DEFAULT_TARGETS[e.target.value] ?? "[]"); }}>{OBJECT_TYPES.map((t) => <option key={t}>{t}</option>)}</select></Field>
        <Field label="Operator notification destination" hint="All three modes always notify — optional, same as every other subscription-shaped callback."><input value={notificationDestination} onChange={(e) => setNotificationDestination(e.target.value)} placeholder="http://operator:8000/autonomy-notify" /></Field>
        <Field label="Expectation targets (JSON array)" hint={parsedTargets ? undefined : <span className="text-bad">must be a JSON array</span>}><textarea rows={2} value={targets} onChange={(e) => setTargets(e.target.value)} spellCheck={false} /></Field>
      </div>
      {selected && <p className="muted small">This instance is <strong>{selected.autonomyMode}</strong> — {
        selected.autonomyMode === "AUTONOMOUS" ? "a real Intent is created immediately, scoped to its pre-configured region."
        : selected.autonomyMode === "ASSIST" ? "this will wait for an operator to supply a region scope before anything is dispatched."
        : "this is observe-only — nothing is ever dispatched."
      }</p>}
      <ActionButton label="Request dispatch" tone="primary" disabled={!parsedTargets || parsedTargets.length === 0 || !instanceId || !rmihId} action={{
        method: "POST", path: "/intent-service/autonomy-dispatches", success: "Autonomy dispatch requested",
        json: {
          instanceId, rmihId, notificationDestination: notificationDestination || null,
          expectations: [expectationOf(objectType, parsedTargets ?? [])],
        },
      }} />
    </Card>
  );
}

function ResolveAutonomyDispatch({ dispatch }: { dispatch: AutonomyDispatch }) {
  const [scope, setScope] = useState("{}");
  const [reason, setReason] = useState("");
  const parsed = parseJsonObject(scope);
  return (
    <div className="row gap end">
      <input className="small" style={{ width: "10rem" }} value={scope} onChange={(e) => setScope(e.target.value)} spellCheck={false} aria-label="Region scope (JSON)" />
      <ActionButton label="Resolve" disabled={!parsed.ok} action={{
        method: "POST", path: `/intent-service/autonomy-dispatches/${dispatch.dispatchId}/resolve`, success: "Dispatch resolved — Intent created",
        json: { regionScope: parsed.ok ? parsed.value : {} },
      }} />
      <Can method="POST" path={`/intent-service/autonomy-dispatches/${dispatch.dispatchId}/reject`}>
        <input className="small" style={{ width: "8rem" }} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="reason" aria-label="Rejection reason" />
        <ActionButton label="Reject" tone="danger" action={{
          method: "POST", path: `/intent-service/autonomy-dispatches/${dispatch.dispatchId}/reject`, success: "Dispatch rejected — nothing dispatched",
          // rejectedBy is pinned to the GUI identity by the BFF
          json: { rejectedBy: "smo-gui", reason: reason || null },
        }} />
      </Can>
    </div>
  );
}
