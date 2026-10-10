/** Sections of the one-record route `/decisions/:decisionId` (`decisions.outcome`, `decisions.why`): the outcome with the job and approval request
 * it belongs to (each opens in a drawer), and why the rApp acted. The page adds the chain and the integrity card from this folder. */
import { useState } from "react";

import type { DecisionRecord } from "../../../api/types";
import { ConfigJobDrawer } from "../../../components/ConfigJobDrawer";
import { Card, Id, KeyValue, StateBadge } from "../../../components/ui";
import { DISPOSITION_MEANING, formatTime } from "../../../lib/domain";
import { ApprovalDrawer } from "../../approvals";

/** The outcome card. */
export function Outcome({ record: r }: { record: DecisionRecord }) {
  const [job, setJob] = useState(false);
  const [approval, setApproval] = useState(false);
  return (
    <Card section="decisions.outcome" title="Outcome">
      <div className="row gap wrap">
        <StateBadge state={r.disposition} />
        <span className="muted small">{DISPOSITION_MEANING[r.disposition]}</span>
      </div>
      <KeyValue items={[
        ["When", formatTime(r.occurredAt)],
        ["rApp", <Id key="i" value={r.invokerId} />],
        ["Requested by", r.requestedBy],
        ["Config job", r.jobId ? <button key="j" type="button" className="btn ghost" onClick={() => setJob(true)}><Id value={r.jobId} /></button> : <span className="muted">none was made</span>],
        ["Approval request", r.approvalId ? <button key="a" type="button" className="btn ghost" onClick={() => setApproval(true)}><Id value={r.approvalId} /></button> : <span className="muted">not held for approval</span>],
        // two-person approval: a record of a request that needed two people carries `approvers` (in order); one without the field reads as before
        [r.approvers ? "Approvers (two were needed)" : "Approved by", r.approvers ? (r.approvers.length ? r.approvers.join(", ") : "none before it ended") : r.approvedBy],
        ["Decided by", r.decidedBy ? `${r.decidedBy} (${formatTime(r.decidedAt)})` : null],
      ]} />
      {job && r.jobId && <ConfigJobDrawer id={r.jobId} onClose={() => setJob(false)} />}
      {approval && r.approvalId && <ApprovalDrawer id={r.approvalId} onClose={() => setApproval(false)} />}
    </Card>
  );
}

/** The "why" card. */
export function Why({ record: r }: { record: DecisionRecord }) {
  return (
    <Card section="decisions.why" title="Why the rApp acted">
      <KeyValue items={[
        ["Rationale", r.rationale],
        ["Model version", r.modelVersion],
        ["Inputs", r.inputsRef],
        ["Its action id", r.actionId],
        ["Managed elements", `${r.managedElements.join(", ")} (${r.changeCount} change${r.changeCount === 1 ? "" : "s"})`],
        ["Correlation id", r.correlationId],
      ]} />
    </Card>
  );
}
