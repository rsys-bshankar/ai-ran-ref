/** Section `intents.dispatches` (Dispatches tab, HISTORY.md OI-6.3 rApp Autonomy Modes): what an rApp instance's onboarding-time autonomy mode
 * did with an inference outcome, as a server-paged table filtered by status on the server; Resolve (with a region scope) or Reject an
 * AWAITING_SCOPE dispatch; and the role-gated "Request autonomy dispatch" form that simulates an rApp asking for one. */
import { useState } from "react";

import type { AutonomyDispatch } from "../../../api/types";
import { ActionButton, Can, Card, Field, Id, StateBadge } from "../../../components/ui";
import { parseJsonObject } from "../../../lib/domain";
import { ServerTable } from "../../../kit/ServerTable";
import { DEFAULT_TARGETS, OBJECT_TYPES, expectationOf, parseTargets } from "../data/forms";
import { DISPATCHES, useHandlers, useInstances } from "../data/queries";

/** The tab. */
export function Dispatches() {
  const [status, setStatus] = useState("");
  return (
    <>
      <Can method="POST" path={DISPATCHES}><RequestDispatch /></Can>
      <Card section="intents.dispatches" title="Autonomy dispatches" actions={
        <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter by status">
          <option value="">All</option><option>AWAITING_SCOPE</option><option>DISPATCHED</option><option>SHADOWED</option><option>REJECTED</option>
        </select>}>
        <p className="muted small">What an rApp instance's own onboarding-time autonomy mode did with an inference outcome — AUTONOMOUS dispatches immediately at a pre-configured scope, ASSIST waits in AWAITING_SCOPE until an operator either scopes it (an Intent is created) or rejects it (nothing is dispatched), SHADOW never dispatches at all.</p>
        <ServerTable<AutonomyDispatch> path={DISPATCHES} query={{ status: status || undefined }} rowKey={(d) => d.dispatchId} empty="No autonomy dispatches." columns={[
          { header: "Dispatch", render: (d) => <Id value={d.dispatchId} /> },
          { header: "Instance", render: (d) => <Id value={d.instanceId} /> },
          { header: "Mode", render: (d) => <StateBadge state={d.autonomyMode} /> },
          { header: "Status", render: (d) => <StateBadge state={d.status} /> },
          { header: "Intent", render: (d) => d.intentId ? <Id value={d.intentId} /> : <span className="muted">—</span> },
          { header: "Rejected", render: (d) => d.rejectedBy ? <span className="small">{d.rejectedBy}{d.rejectionReason ? ` — ${d.rejectionReason}` : ""}</span> : <span className="muted">—</span> },
          { header: "", className: "actions", render: (d) => d.status === "AWAITING_SCOPE" && <ResolveDispatch dispatch={d} /> },
        ]} />
      </Card>
    </>
  );
}

/** Simulate an rApp instance asking for its inference outcome to be enacted. */
function RequestDispatch() {
  const instances = useInstances();
  const handlers = useHandlers();
  const [instanceId, setInstanceId] = useState("");
  const [rmihId, setRmihId] = useState("");
  const [objectType, setObjectType] = useState("RAN_SUBNETWORK");
  const [targets, setTargets] = useState(DEFAULT_TARGETS.RAN_SUBNETWORK);
  const [notificationDestination, setNotificationDestination] = useState("");
  const parsedTargets = parseTargets(targets);
  const selected = instances.data?.find((i) => i.instanceId === instanceId);
  return (
    <Card section="intents.requestDispatch" title="Request autonomy dispatch">
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
        method: "POST", path: DISPATCHES, success: "Autonomy dispatch requested",
        json: { instanceId, rmihId, notificationDestination: notificationDestination || null, expectations: [expectationOf(objectType, parsedTargets ?? [])] },
      }} />
    </Card>
  );
}

/** Resolve an AWAITING_SCOPE dispatch with a region scope (an Intent is created), or reject it with a reason. */
function ResolveDispatch({ dispatch }: { dispatch: AutonomyDispatch }) {
  const [scope, setScope] = useState("{}");
  const [reason, setReason] = useState("");
  const parsed = parseJsonObject(scope);
  return (
    <div className="row gap end">
      <input className="small" style={{ width: "10rem" }} value={scope} onChange={(e) => setScope(e.target.value)} spellCheck={false} aria-label="Region scope (JSON)" />
      <ActionButton label="Resolve" disabled={!parsed.ok} action={{
        method: "POST", path: `${DISPATCHES}/${dispatch.dispatchId}/resolve`, success: "Dispatch resolved — Intent created",
        json: { regionScope: parsed.ok ? parsed.value : {} },
      }} />
      <Can method="POST" path={`${DISPATCHES}/${dispatch.dispatchId}/reject`}>
        <input className="small" style={{ width: "8rem" }} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="reason" aria-label="Rejection reason" />
        <ActionButton label="Reject" tone="danger" action={{
          method: "POST", path: `${DISPATCHES}/${dispatch.dispatchId}/reject`, success: "Dispatch rejected — nothing dispatched",
          // rejectedBy is pinned to the GUI identity by the BFF
          json: { rejectedBy: "smo-gui", reason: reason || null },
        }} />
      </Can>
    </div>
  );
}
