/** Section `intents.reports`: the drawer of one intent, opened from "Reports" on a card or a table row: its state and actions, its facts, its
 * TS 28.312 expectations as JSON, every report the handler published (newest first), and the admin-only "publish a report as the handling RMIH"
 * tool (`POST /intent-service/intent-reports`), which lets an admin simulate fulfilment and conflict reports. */
import { useState } from "react";

import type { Intent } from "../../../api/types";
import { ActionButton, Can, Drawer, Field, Id, Json, KeyValue, StateBadge } from "../../../components/ui";
import { formatTime, splitList } from "../../../lib/domain";
import { REPORTS, useIntentReports } from "../data/queries";
import { IntentActions } from "./IntentActions";

/** The drawer. */
export function IntentReports({ intent, onClose }: { intent: Intent; onClose: () => void }) {
  const reports = useIntentReports(intent.intentId, 100);
  return (
    <Drawer title={<>Intent <Id value={intent.intentId} /></>} onClose={onClose}>
      <div className="row between"><StateBadge state={intent.intentAdminState} /><IntentActions intent={intent} /></div>
      <KeyValue items={[["Intent ID", <code>{intent.intentId}</code>], ["Label", intent.userLabel ?? "—"], ["RMIO", intent.rmioId], ["Handler", intent.rmihId], ["Priority", intent.intentPriority], ["Purpose", intent.intentMgmtPurpose]]} />
      <h3>Expectations</h3>
      <Json value={intent.attributes?.intentExpectations ?? []} />
      <h3>Intent reports</h3>
      <Can method="POST" path={REPORTS}><PublishIntentReport intentId={intent.intentId} /></Can>
      {reports.isLoading ? <p className="muted">Loading…</p> : (reports.data ?? []).length === 0 ? <p className="muted">No reports published by a handler yet.</p> : reports.data!.map((r) => (
        <div key={r.reportId} className="report">
          <div className="muted small">{formatTime(r.attributes.lastUpdatedTime)}</div>
          <Json value={r.attributes} />
        </div>
      ))}
    </Drawer>
  );
}

/** Publish a fulfilment report (and optional conflicts) for the intent, as its handler would. */
function PublishIntentReport({ intentId }: { intentId: string }) {
  const [status, setStatus] = useState("FULFILLED");
  const [conflicting, setConflicting] = useState("");
  return (
    <details className="admin-tools">
      <summary>Admin: publish a report as the handling RMIH</summary>
      <div className="form inline">
        <Field label="Fulfilment" hint="FULFILLED, or NOT_FULFILLED with its TS 28.312 state"><select value={status} onChange={(e) => setStatus(e.target.value)}>{["FULFILLED", "RECEIVED", "DEGRADED", "SUSPENDED", "TERMINATED"].map((s) => <option key={s}>{s}</option>)}</select></Field>
        <Field label="Conflicting intents" hint="Comma-separated intent ids"><input value={conflicting} onChange={(e) => setConflicting(e.target.value)} /></Field>
      </div>
      <ActionButton label="Publish report" action={{
        method: "POST", path: REPORTS, success: "Report published",
        json: {
          intentReference: intentId,
          intentFulfilmentReport: { intentFulfilmentInfo: status === "FULFILLED" ? { fulfilmentStatus: "FULFILLED" } : { fulfilmentStatus: "NOT_FULFILLED", notFullfilledState: status } },
          intentConflictReports: splitList(conflicting).length
            ? splitList(conflicting).map((c, i) => ({ conflictId: `c${i + 1}`, conflictType: "INTENT_CONFLICT", conflictingIntent: c })) : null,
        },
      }} />
    </details>
  );
}
