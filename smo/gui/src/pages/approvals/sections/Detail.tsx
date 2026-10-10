/** Section `approvals.detail`: one request — title, status, countdown to its lapse, the facts, then the impact tiles, the config diff, why the
 * rApp asks and the decision box. `ApprovalDetailPanel` is the page's right-hand panel; `ApprovalDrawer` is the same content in a drawer, used by
 * the Decided tab and by the Decisions page (a decision record's approval request). */
import { useState } from "react";

import type { ApprovalDetail } from "../../../api/types";
import { ConfigJobDrawer } from "../../../components/ConfigJobDrawer";
import { Card, Drawer, ErrorBox, Id, KeyValue, StateBadge } from "../../../components/ui";
import { Empty, Skeleton } from "../../../kit/states";
import { APPROVAL_MEANING, describeElements, formatTime, timeLeft } from "../../../lib/domain";
import { countdown, useApproval, useApprovalRecord } from "../data/queries";
import { useNow } from "../data/useNow";
import { ChangeDiff } from "./ChangeDiff";
import { DecisionBox } from "./DecisionBox";
import { ImpactTiles } from "./ImpactTiles";
import { Rationale } from "./Rationale";

/** The content of one request, wherever it is shown. */
export function ApprovalBody({ id }: { id: string }) {
  const view = useApproval(id);
  const data = view.data;
  const record = useApprovalRecord(id, data !== undefined && data.status !== "PENDING");
  const [job, setJob] = useState<string | null>(null);
  if (view.error && !data) return <ErrorBox error={view.error} />;
  if (!data) return <Skeleton lines={5} />;
  return (
    <div className="stack">
      <Header approval={data} onJob={setJob} />
      <ImpactTiles approval={data} />
      <ChangeDiff approval={data} />
      <Rationale approval={data} record={record.data?.[0]} />
      <DecisionBox key={data.approvalId} approval={data} />
      {job && <ConfigJobDrawer id={job} onClose={() => setJob(null)} />}
    </div>
  );
}

/** Title, status, countdown and the request's facts. */
function Header({ approval: a, onJob }: { approval: ApprovalDetail; onJob: (id: string) => void }) {
  const now = useNow(1_000);
  const pending = a.status === "PENDING";
  return (
    <div className="stack approvals-head">
      <div className="row between wrap">
        <div className="col">
          <span className="eyebrow">rApp action · <Id value={a.approvalId} /></span>
          <h3>{a.requestedBy} wants to make {a.changeCount} change{a.changeCount === 1 ? "" : "s"} on {describeElements(a.managedElements)}</h3>
        </div>
        {pending && <div className="col right"><span className="approvals-clock" aria-label="Time left">{countdown(a.expiresAt, now)}</span><span className="small muted">until it lapses</span></div>}
      </div>
      <div className="row gap wrap"><StateBadge state={a.status} /><span className="muted small">{APPROVAL_MEANING[a.status]}</span></div>
      <KeyValue items={[
        ["rApp", <Id key="i" value={a.invokerId} />],
        ["Requested by", a.requestedBy],
        ["Asked", formatTime(a.createdAt)],
        [pending ? "Lapses" : "Decided", pending ? `${formatTime(a.expiresAt)} (${timeLeft(a.expiresAt, now)}), then it ${a.onTimeout === "REJECT" ? "is rejected" : "expires"}` : `${formatTime(a.decidedAt)} by ${a.decidedBy ?? "—"}`],
        ["Reason", a.decisionReason],
        ["Refused as", a.refusalCode],
        ["Config job", a.jobId ? <button key="j" type="button" className="btn ghost" onClick={() => onJob(a.jobId!)}><Id value={a.jobId} /></button> : null],
      ]} />
    </div>
  );
}

/** The page's detail panel; `id` null asks the operator to pick a request. */
export function ApprovalDetailPanel({ id }: { id: string | null }) {
  return (
    <Card section="approvals.detail">
      {id ? <ApprovalBody key={id} id={id} /> : <Empty title="No request selected.">Pick one from the queue.</Empty>}
    </Card>
  );
}

/** One request in a drawer (the Decided tab, the Decisions page). */
export function ApprovalDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  return (
    <Drawer title={<>rApp action <Id value={id} /></>} onClose={onClose}>
      <ApprovalBody id={id} />
    </Drawer>
  );
}
