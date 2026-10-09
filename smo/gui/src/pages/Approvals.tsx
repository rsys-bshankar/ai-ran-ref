import { useState } from "react";
import { Link } from "react-router-dom";

import { useSmo, useSmoAction, useSmoPage } from "../api/hooks";
import { useAuth } from "../auth/AuthContext";
import type { Approval, ApprovalDetail, DecisionRecord } from "../api/types";
import { ConfigJobDrawer } from "../components/ConfigJobDrawer";
import { Card, Can, DataTable, Drawer, ErrorBox, Field, Id, KeyValue, PageHeader, StateBadge, Tabs, useHashTab } from "../components/ui";
import { APPROVAL_MEANING, approvalProgress, decidedByText, describeChange, describeElements, formatTime, timeLeft } from "../lib/domain";

const TABS = ["waiting", "decided"] as const;
const WAITING = "/ran-nf-oam/rapp-approvals";

/** The approval inbox (PR-GUI-7): what is waiting for a person's decision. Today that is the actions of rApps held for approval (GUI-7.2, AI-11);
 * change-window approvals (GUI-7.1, needs MGT-4) and model gate approvals (GUI-7.3) are not built, and the page says so. */
export function Approvals() {
  const [tab, setTab] = useHashTab(TABS, "waiting");
  const waiting = useSmoPage<Approval>(WAITING, { status: "PENDING", limit: 100, total: false }, { refetchInterval: 5_000 });
  const count = waiting.data?.items.length ?? 0;
  return (
    <>
      <PageHeader title="Approvals" subtitle="What is waiting for a person to decide before anything is written to the network" />
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: "waiting", label: <>rApp actions{count > 0 && <span className="badge tone-warn" title={`${count} waiting`}> {count}</span>}</> },
        { id: "decided", label: "Decided" },
      ]} />
      {tab === "waiting" && <Waiting />}
      {tab === "decided" && <Decided />}
      <p className="muted small">
        Not in this inbox yet: change-window approvals of CM jobs and model gate approvals (<code>PR-GUI-7</code> steps 7.1 and 7.3). An rApp appears here when its
        instance was created with an approval policy (ASSIST mode) or an admin set one; every other rApp writes at once, as before.
      </p>
    </>
  );
}

function Waiting() {
  const waiting = useSmoPage<Approval>(WAITING, { status: "PENDING", limit: 100, total: false }, { refetchInterval: 5_000 });
  const [open, setOpen] = useState<string | null>(null);
  return (
    <Card title="Waiting for a decision">
      <p className="muted small">Nothing below has been written. A request that nobody decides lapses at its time (the policy of the rApp says whether it expires or is rejected) and writes nothing.</p>
      <DataTable rows={waiting.data?.items} rowKey={(a) => a.approvalId} error={waiting.error} loading={waiting.isLoading} empty="Nothing is waiting for a decision."
        onRowClick={(a) => setOpen(a.approvalId)} columns={[
          { header: "rApp", render: (a) => <><Id value={a.invokerId} /> <span className="muted small">{a.requestedBy}</span></> },
          { header: "Changes", render: (a) => <span title={a.managedElements.join(", ")}>{a.changeCount} on {describeElements(a.managedElements)}</span> },
          { header: "Why", render: (a) => <span className="small">{a.decision?.rationale ?? <span className="muted">no rationale given</span>}</span> },
          { header: "Approvals", render: (a) => approvalProgress(a) ?? <span className="muted">one needed</span> },
          { header: "Asked", render: (a) => formatTime(a.createdAt) },
          { header: "Lapses", render: (a) => <span title={formatTime(a.expiresAt)}>{timeLeft(a.expiresAt)} <span className="muted small">({a.onTimeout === "REJECT" ? "rejected" : "expires"})</span></span> },
          { header: "", render: (a) => <button className="btn" onClick={(e) => { e.stopPropagation(); setOpen(a.approvalId); }}>Review…</button> },
        ]} />
      {open && <ApprovalDrawer id={open} onClose={() => setOpen(null)} />}
    </Card>
  );
}

function Decided() {
  const decided = useSmoPage<Approval>(WAITING, { limit: 100, total: false });
  const [open, setOpen] = useState<string | null>(null);
  const rows = decided.data?.items.filter((a) => a.status !== "PENDING");
  return (
    <Card title="Decided and lapsed">
      <DataTable rows={rows} rowKey={(a) => a.approvalId} error={decided.error} loading={decided.isLoading} empty="No request has been decided yet." onRowClick={(a) => setOpen(a.approvalId)} columns={[
        { header: "rApp", render: (a) => <><Id value={a.invokerId} /> <span className="muted small">{a.requestedBy}</span></> },
        { header: "Changes", render: (a) => `${a.changeCount} on ${describeElements(a.managedElements)}` },
        { header: "Outcome", render: (a) => <span title={APPROVAL_MEANING[a.status]}><StateBadge state={a.status} /></span> },
        { header: "By", render: (a) => decidedByText(a) },
        { header: "When", render: (a) => formatTime(a.decidedAt) },
        { header: "Reason", render: (a) => a.decisionReason ?? a.refusalCode ?? <span className="muted">—</span> },
      ]} />
      {open && <ApprovalDrawer id={open} onClose={() => setOpen(null)} />}
    </Card>
  );
}

/** One request: the changes it asks for, why the rApp asks, and (while it waits) Approve and Reject with a reason. Approve runs the checks again and writes. */
export function ApprovalDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const view = useSmo<ApprovalDetail>(`${WAITING}/${id}`, undefined, { refetchInterval: 5_000 });
  const record = useSmo<DecisionRecord[]>("/ran-nf-oam/decision-records", { approval_id: id, limit: 1 }, { enabled: view.data !== undefined && view.data.status !== "PENDING" });
  const [reason, setReason] = useState("");
  const [job, setJob] = useState<string | null>(null);
  const action = useSmoAction();
  const { me } = useAuth();
  const data = view.data;
  const needed = data?.requiredApprovals ?? 1;
  const given = data?.approvals ?? [];
  const iApproved = me !== null && given.some((v) => v.by.trim().toLowerCase() === `smo-gui:${me.username}`.toLowerCase());
  const lastApproval = given.length + 1 >= needed;
  const decide = (verb: "approve" | "reject") => action.mutate(
    { method: "POST", path: `${WAITING}/${id}/${verb}`, json: { reason: reason.trim() || null },
      success: verb === "reject" ? "Rejected: nothing was written" : lastApproval ? "Approved: the change is being written" : "Your approval is recorded: another person must approve before anything is written" },
    { onSuccess: () => setReason("") });
  return (
    <Drawer title={<>rApp action <Id value={id} /></>} onClose={onClose}>
      <ErrorBox error={view.error} />
      {data && <>
        <div className="row gap wrap">
          <StateBadge state={data.status} />
          <span className="muted small">{APPROVAL_MEANING[data.status]}</span>
        </div>
        <KeyValue items={[
          ["rApp", <Id key="i" value={data.invokerId} />],
          ["Requested by", data.requestedBy],
          ["Asked", formatTime(data.createdAt)],
          [data.status === "PENDING" ? "Lapses" : "Decided", data.status === "PENDING" ? `${formatTime(data.expiresAt)} (${timeLeft(data.expiresAt)}), then it ${data.onTimeout === "REJECT" ? "is rejected" : "expires"}` : `${formatTime(data.decidedAt)} by ${data.decidedBy ?? "—"}`],
          ["Approvals", needed > 1 ? `${given.length} of ${needed} (two different people must approve)` : null],
          ["Reason", data.decisionReason],
          ["Refused as", data.refusalCode],
          ["Config job", data.jobId ? <button key="j" className="btn ghost" onClick={() => setJob(data.jobId)}><Id value={data.jobId} /></button> : null],
        ]} />

        {needed > 1 && (
          <Card title={`Approvals so far (${given.length} of ${needed})`}>
            {given.length === 0
              ? <p className="muted small">None yet. This rApp's policy asks for {needed} different people: the first approval keeps the request waiting, the second makes the change. One rejection ends it.</p>
              : <ul aria-label="Approvals so far">{given.map((v, i) => <li key={i} className="small"><b>{v.by}</b> <span className="muted">{formatTime(v.at)}{v.reason ? ` · ${v.reason}` : ""}</span></li>)}</ul>}
          </Card>
        )}

        <Card title="Why the rApp asks">
          <KeyValue items={[
            ["Rationale", data.decision?.rationale],
            ["Model version", data.decision?.modelVersion],
            ["Inputs", data.decision?.inputsRef],
            ["Its action id", data.decision?.actionId],
          ]} />
          {record.data?.[0] && <p className="small"><Link to={`/decisions/${record.data[0].decisionId}`}>Open the decision record →</Link></p>}
        </Card>

        <Card title={`What it would write (${data.changes.length})`}>
          <ul aria-label="Changes">{data.changes.map((c, i) => <li key={i} className="small"><code>{describeChange(c)}</code></li>)}</ul>
          {data.accessScope && <p className="muted small">Access scope: {data.accessScope}</p>}
        </Card>

        {data.status === "PENDING" && (
          <Can method="POST" path={`${WAITING}/${id}/approve`}>
            <Card title="Your decision">
              <p className="muted small">Approving checks the rApp's safeguards again (it may have been stopped or reached a limit while this waited), then writes exactly the changes above. It is recorded under your name.</p>
              {needed > 1 && <p className="small" role="note">{iApproved
                ? "You have approved this request. A different person must give the other approval; you can still reject it."
                : lastApproval ? "Your approval is the last one needed: approving writes the change." : "This is the first of two approvals: nothing is written until a different person approves too."}</p>}
              <Field label="Reason" hint="Optional; kept with the decision"><input value={reason} maxLength={1000} onChange={(e) => setReason(e.target.value)} /></Field>
              <div className="row gap">
                <button className="btn primary" disabled={action.isPending || iApproved} title={iApproved ? "You have already approved this request" : undefined} onClick={() => decide("approve")}>{action.isPending ? "…" : "Approve"}</button>
                <button className="btn danger" disabled={action.isPending} onClick={() => decide("reject")}>Reject</button>
              </div>
            </Card>
          </Can>
        )}
      </>}
      {job && <ConfigJobDrawer id={job} onClose={() => setJob(null)} />}
    </Drawer>
  );
}
