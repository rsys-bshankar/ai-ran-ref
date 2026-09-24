import { useState } from "react";

import { useSmo, useSmoAction } from "../api/hooks";
import type { A1Policy, Intent, IntentReport, Rmih } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { ActionButton, Can, Card, DataTable, Drawer, Field, Id, Json, KeyValue, PageHeader, StateBadge, Tabs, useHashTab } from "../components/ui";
import { formatTime, parseJsonObject } from "../lib/domain";

const TABS = ["a1", "intents", "handlers"] as const;

export function Policy() {
  const [tab, setTab] = useHashTab(TABS, "a1");
  return (
    <>
      <PageHeader title="Policy & Intents" subtitle="A1 policies enforced at the Near-RT RIC, and TS 28.312 intents dispatched to intent handlers" />
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "a1", label: "A1 policies" }, { id: "intents", label: "Intents" }, { id: "handlers", label: "Intent handlers (RMIH)" }]} />
      {tab === "a1" && <A1Policies />}
      {tab === "intents" && <Intents />}
      {tab === "handlers" && <Handlers />}
    </>
  );
}

// ---------------------------------------------------------------- A1

function A1Policies() {
  const [type, setType] = useState("");
  const policies = useSmo<A1Policy[]>("/a1-related/policies", { policy_type_id: type });
  const types = useSmo<{ policyTypeId: string; nearRtRicId: string }[]>("/a1-related/policy-types");
  const [selected, setSelected] = useState<A1Policy | null>(null);
  return (
    <>
      <Can method="POST" path="/a1-related/policies"><CreatePolicy types={types.data ?? []} /></Can>
      <Card title="A1 policies" actions={<select value={type} onChange={(e) => setType(e.target.value)} aria-label="Policy type"><option value="">All policy types</option>{types.data?.map((t) => <option key={t.policyTypeId}>{t.policyTypeId}</option>)}</select>}>
        <DataTable rows={policies.data} loading={policies.isLoading} error={policies.error} rowKey={(p) => p.policyId} empty="No A1 policies."
          onRowClick={setSelected} selectedKey={selected?.policyId} columns={[
            { header: "Policy", render: (p) => <Id value={p.policyId} /> },
            { header: "Type", render: (p) => <code>{p.policyTypeId}</code> },
            { header: "Near-RT RIC", render: (p) => p.nearRtRicId },
            { header: "Enforcement", render: (p) => <StateBadge state={p.enforcementStatus} /> },
            { header: "", className: "actions", render: (p) => <ActionButton label="Delete" tone="danger" confirm="Delete this A1 policy at the Near-RT RIC?" action={{ method: "DELETE", path: `/a1-related/policies/${p.policyId}`, success: "Policy deleted" }} /> },
          ]} />
      </Card>
      {selected && <PolicyDrawer policy={selected} onClose={() => setSelected(null)} />}
    </>
  );
}

function CreatePolicy({ types }: { types: { policyTypeId: string; nearRtRicId: string }[] }) {
  const { me } = useAuth();
  const [policyTypeId, setPolicyTypeId] = useState("");
  const [ric, setRic] = useState("mock-near-rt-ric-001");
  const [body, setBody] = useState('{"scope": {"cellId": "cell-1"}, "qosObjectives": {"gfbr": 100}}');
  const parsed = parseJsonObject(body);
  return (
    <Card title="Create A1 policy">
      <div className="form grid cols-3 tight">
        <Field label="Policy type"><select value={policyTypeId} onChange={(e) => setPolicyTypeId(e.target.value)}><option value="">Choose…</option>{types.map((t) => <option key={t.policyTypeId}>{t.policyTypeId}</option>)}</select></Field>
        <Field label="Near-RT RIC"><input value={ric} onChange={(e) => setRic(e.target.value)} /></Field>
        <Field label="Creator"><input value={`smo-gui:${me?.username}`} disabled /></Field>
        <Field label="Policy object (JSON)" hint={parsed.ok ? "The Near-RT RIC rejects a byte-identical policy object under the same type." : <span className="text-bad">{parsed.error}</span>}>
          <textarea rows={3} value={body} onChange={(e) => setBody(e.target.value)} spellCheck={false} />
        </Field>
      </div>
      <ActionButton label="Create policy" tone="primary" disabled={!policyTypeId || !parsed.ok} action={{
        method: "POST", path: "/a1-related/policies", success: "Policy sent to the Near-RT RIC",
        json: { policyTypeId, nearRtRicId: ric, creatorId: `smo-gui:${me?.username}`, policyObject: parsed.ok ? parsed.value : {} },
      }} />
    </Card>
  );
}

function PolicyDrawer({ policy, onClose }: { policy: A1Policy; onClose: () => void }) {
  const { can } = useAuth();
  const status = useSmo<{ policyId: string; enforcementStatus: string }>(`/a1-related/policies/${policy.policyId}/status`, undefined, { refetchInterval: false });
  const [text, setText] = useState(JSON.stringify(policy.policyObject, null, 2));
  const parsed = parseJsonObject(text);
  const update = useSmoAction();
  const path = `/a1-related/policies/${policy.policyId}`;
  return (
    <Drawer title={<>A1 policy <Id value={policy.policyId} /></>} onClose={onClose}>
      <KeyValue items={[
        ["Policy ID", <code>{policy.policyId}</code>], ["Type", policy.policyTypeId], ["Near-RT RIC", policy.nearRtRicId],
        ["Enforcement (stored)", <StateBadge state={policy.enforcementStatus} />],
        ["Enforcement (live from RIC)", status.isLoading ? "checking…" : status.error ? <span className="text-bad">{(status.error as Error).message}</span> : <StateBadge state={status.data?.enforcementStatus} />],
      ]} />
      <h3>Policy object</h3>
      {!can("PUT", path) && <Json value={policy.policyObject} />}
      <Can method="PUT" path={path}>
        <textarea rows={8} value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} />
        {!parsed.ok && <p className="text-bad small">{parsed.error}</p>}
        <div className="row end"><button className="btn primary" disabled={!parsed.ok || update.isPending}
          onClick={() => parsed.ok && update.mutate({ method: "PUT", path, json: parsed.value, success: "Policy updated at the Near-RT RIC" })}>Update</button></div>
      </Can>
    </Drawer>
  );
}

// ---------------------------------------------------------------- intents

const PURPOSES = ["FULFILMENT_WITHOUT_NEGOTIATION", "FULFILMENT_WITH_NEGOTIATION", "FEASIBILITYCHECK", "FEASIBILITYCHECK_WITH_RECOMMENDATIONS", "EXPLORATION"];
const OBJECT_TYPES = ["RAN_SUBNETWORK", "EDGE_SERVICE_SUPPORT", "5GC_SUBNETWORK", "RADIO_SERVICE", "SUBNETWORK"];

function Intents() {
  const [state, setState] = useState("");
  const intents = useSmo<Intent[]>("/policy-mgmt/intents", { admin_state: state });
  const [selected, setSelected] = useState<Intent | null>(null);
  return (
    <>
      <Can method="POST" path="/policy-mgmt/intents"><CreateIntent /></Can>
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
  const path = `/policy-mgmt/intents/${intent.intentId}`;
  const next = intent.intentAdminState === "ACTIVATED" ? "DEACTIVATED" : "ACTIVATED";
  return (
    <div className="row gap end">
      {intent.rmioId === "smo-gui" && <ActionButton label={next === "ACTIVATED" ? "Activate" : "Deactivate"} action={{ method: "PATCH", path: `${path}/admin-state`, json: { newState: next }, success: `Intent ${next}` }} />}
      <ActionButton label="Delete" tone="danger" confirm="Delete (retract) this intent?" action={{ method: "DELETE", path, success: "Intent deleted" }} />
    </div>
  );
}

function CreateIntent() {
  const [objectType, setObjectType] = useState("RAN_SUBNETWORK");
  const [targets, setTargets] = useState('[{"targetName": "DLThptPerUE", "targetCondition": "IS_GREATER_THAN", "targetValueRange": 50}]');
  const [priority, setPriority] = useState("1");
  const [purpose, setPurpose] = useState(PURPOSES[0]);
  const [scope, setScope] = useState("");
  let parsedTargets: unknown[] | null = null;
  try { const v = JSON.parse(targets); parsedTargets = Array.isArray(v) ? v : null; } catch { parsedTargets = null; }
  return (
    <Card title="Create intent">
      <p className="muted small">Dispatched to every registered handler whose <code>supportedExpectationObjectType</code> matches the expectation's <code>expectationObject.objectType</code>.</p>
      <div className="form grid cols-3 tight">
        <Field label="Expectation object type"><select value={objectType} onChange={(e) => setObjectType(e.target.value)}>{OBJECT_TYPES.map((t) => <option key={t}>{t}</option>)}</select></Field>
        <Field label="Priority"><input type="number" min={1} value={priority} onChange={(e) => setPriority(e.target.value)} /></Field>
        <Field label="Handling scope"><select value={scope} onChange={(e) => setScope(e.target.value)}><option value="">any</option><option>RAN</option><option>CN</option></select></Field>
        <Field label="Purpose"><select value={purpose} onChange={(e) => setPurpose(e.target.value)}>{PURPOSES.map((p) => <option key={p}>{p}</option>)}</select></Field>
        <Field label="Expectation targets (JSON array)" hint={parsedTargets ? undefined : <span className="text-bad">must be a JSON array</span>}><textarea rows={2} value={targets} onChange={(e) => setTargets(e.target.value)} spellCheck={false} /></Field>
      </div>
      <ActionButton label="Create intent" tone="primary" disabled={!parsedTargets} action={{
        method: "POST", path: "/policy-mgmt/intents", success: "Intent created",
        json: {
          expectations: [{ expectationVerb: "DELIVER", expectationObject: { objectType }, expectationTargets: parsedTargets ?? [] }],
          priority: Number(priority) || 1, intentMgmtPurpose: purpose, intentHandlingScope: scope || null,
        },
      }} />
    </Card>
  );
}

function IntentDrawer({ intent, onClose }: { intent: Intent; onClose: () => void }) {
  const reports = useSmo<IntentReport[]>("/policy-mgmt/intent-reports", { intent_id: intent.intentId });
  return (
    <Drawer title={<>Intent <Id value={intent.intentId} /></>} onClose={onClose}>
      <div className="row between"><StateBadge state={intent.intentAdminState} /><IntentActions intent={intent} /></div>
      <KeyValue items={[["Intent ID", <code>{intent.intentId}</code>], ["RMIO", intent.rmioId], ["Priority", intent.intentPriority], ["Purpose", intent.intentMgmtPurpose]]} />
      <h3>Fulfilment / conflict reports</h3>
      {(reports.data ?? []).length === 0 ? <p className="muted">No reports published by a handler yet.</p> : reports.data!.map((r) => (
        <div key={r.reportId} className="report">
          <div className="muted small">{formatTime(r.lastUpdatedTime)}</div>
          <Json value={{ fulfilmentReport: r.fulfilmentReport, conflictReports: r.conflictReports }} />
        </div>
      ))}
    </Drawer>
  );
}

function Handlers() {
  const handlers = useSmo<Rmih[]>("/policy-mgmt/intent-handling-functions");
  return (
    <Card title="Registered intent handlers" actions={<span className="muted small">Framework-internal only (SO SMOS / SA SMOS) — D-SEC-POLICY-1</span>}>
      <DataTable rows={handlers.data} loading={handlers.isLoading} error={handlers.error} rowKey={(h) => h.rmihId} empty="No intent handlers registered." columns={[
        { header: "RMIH", render: (h) => <strong>{h.rmihId}</strong> },
        { header: "Supported object types", render: (h) => h.capabilities.map((c) => String(c.supportedExpectationObjectType ?? "?")).join(", ") },
        { header: "Scope", render: (h) => h.intentHandlingScope?.join(", ") ?? "any" },
        { header: "Callback", render: (h) => <code className="small">{h.notificationCallbackUri}</code> },
      ]} />
    </Card>
  );
}
