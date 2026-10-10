import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";

import { useSmo, useSmoPage } from "../api/hooks";
import type { DecisionRecord } from "../api/types";
import { ConfigJobDrawer } from "../components/ConfigJobDrawer";
import { Card, DataTable, ErrorBox, Field, Id, KeyValue, PageHeader, StateBadge } from "../components/ui";
import { DISPOSITION_MEANING, INTEGRITY_MEANING, decisionQuery, formatTime } from "../lib/domain";
import { ApprovalDrawer } from "./Approvals";

const PAGE = 25;
const DISPOSITIONS = ["DIRECT", "APPROVED", "ROLLBACK", "REJECTED", "EXPIRED", "REFUSED"] as const;

/** Why rApps acted (PR-AI-13.4): one record per config job an rApp made, and per request that ended without one. Filters and pages; `?job=` and
 * `?approval=` in the address narrow it to one job or one approval request. */
export function Decisions() {
  const [params] = useSearchParams();
  const [form, setForm] = useState({ invoker: "", disposition: "", model: "" });
  const [offset, setOffset] = useState(0);
  const query = decisionQuery({ ...form, job: params.get("job") ?? "", approval: params.get("approval") ?? "" }, offset, PAGE);
  const page = useSmoPage<DecisionRecord>("/ran-nf-oam/decision-records", query);
  const change = (patch: Partial<typeof form>) => { setForm({ ...form, ...patch }); setOffset(0); };
  return (
    <>
      <PageHeader title="Decisions" subtitle="Why each rApp change was made: the inputs it decided on, the model version, its rationale, the job it became and who approved it" />
      <Card>
        <div className="row gap wrap">
          <Field label="rApp (invoker id)"><input aria-label="Filter by rApp" value={form.invoker} onChange={(e) => change({ invoker: e.target.value })} /></Field>
          <Field label="Outcome">
            <select aria-label="Filter by outcome" value={form.disposition} onChange={(e) => change({ disposition: e.target.value })}>
              <option value="">All</option>
              {DISPOSITIONS.map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </Field>
          <Field label="Model version"><input aria-label="Filter by model version" value={form.model} onChange={(e) => change({ model: e.target.value })} /></Field>
        </div>
        {(params.get("job") || params.get("approval")) && (
          <p className="small">Narrowed to {params.get("job") ? <>job <Id value={params.get("job")} /></> : <>request <Id value={params.get("approval")} /></>}. <Link to="/decisions">Show all</Link></p>
        )}
      </Card>
      <Card>
        <DataTable rows={page.data?.items} rowKey={(r) => r.decisionId} error={page.error} loading={page.isLoading} empty="No decision has been recorded for these filters."
          columns={[
            { header: "When", render: (r) => <Link to={`/decisions/${r.decisionId}`}>{formatTime(r.occurredAt)}</Link> },
            { header: "rApp", render: (r) => <Id value={r.invokerId} /> },
            { header: "Outcome", render: (r) => <span title={DISPOSITION_MEANING[r.disposition]}><StateBadge state={r.disposition} /></span> },
            { header: "Model", render: (r) => r.modelVersion ?? <span className="muted">—</span> },
            { header: "Rationale", render: (r) => <span className="small">{r.rationale ?? <span className="muted">none given</span>}</span> },
            { header: "Changes", render: (r) => r.changeCount },
            { header: "Approved by", render: (r) => r.approvers?.length ? r.approvers.join(", ") : r.approvedBy ?? <span className="muted">—</span> },
          ]} />
        <div className="row gap">
          <button className="btn" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>Newer</button>
          <button className="btn" disabled={!page.data?.hasMore} onClick={() => setOffset(offset + PAGE)}>Older</button>
          <span className="muted small">{page.data ? `${offset + 1}–${offset + page.data.items.length}` : ""}</span>
        </div>
      </Card>
    </>
  );
}

/** One decision record, with the result of checking it against the audit chain, and the job and approval request it belongs to. */
export function DecisionDetail() {
  const { decisionId } = useParams();
  const record = useSmo<DecisionRecord>(decisionId ? `/ran-nf-oam/decision-records/${decisionId}` : null);
  const [job, setJob] = useState(false);
  const [approval, setApproval] = useState(false);
  const r = record.data;
  return (
    <>
      <PageHeader title="Decision" subtitle={<><Id value={decisionId} /> · <Link to="/decisions">← All decisions</Link></>} />
      <ErrorBox error={record.error} />
      {r && <>
        <Card title="Outcome">
          <div className="row gap wrap">
            <StateBadge state={r.disposition} />
            <span className="muted small">{DISPOSITION_MEANING[r.disposition]}</span>
          </div>
          <KeyValue items={[
            ["When", formatTime(r.occurredAt)],
            ["rApp", <Id key="i" value={r.invokerId} />],
            ["Requested by", r.requestedBy],
            ["Config job", r.jobId ? <button key="j" className="btn ghost" onClick={() => setJob(true)}><Id value={r.jobId} /></button> : <span className="muted">none was made</span>],
            ["Approval request", r.approvalId ? <button key="a" className="btn ghost" onClick={() => setApproval(true)}><Id value={r.approvalId} /></button> : <span className="muted">not held for approval</span>],
            [r.approvers ? "Approvers (two were needed)" : "Approved by", r.approvers ? (r.approvers.length ? r.approvers.join(", ") : "none before it ended") : r.approvedBy],
            ["Decided by", r.decidedBy ? `${r.decidedBy} (${formatTime(r.decidedAt)})` : null],
          ]} />
        </Card>
        <Card title="Why the rApp acted">
          <KeyValue items={[
            ["Rationale", r.rationale],
            ["Model version", r.modelVersion],
            ["Inputs", r.inputsRef],
            ["Its action id", r.actionId],
            ["Managed elements", `${r.managedElements.join(", ")} (${r.changeCount} change${r.changeCount === 1 ? "" : "s"})`],
            ["Correlation id", r.correlationId],
          ]} />
        </Card>
        <Card title="Integrity">
          <div className="row gap wrap" role="status">
            <StateBadge state={r.integrity?.status} />
            <span className="small">{r.integrity ? INTEGRITY_MEANING[r.integrity.status] : ""}</span>
          </div>
          {r.integrity?.reason && <p className="small">{r.integrity.reason}</p>}
          <KeyValue items={[["Record hash", <code key="h" className="id">{r.contentHash}</code>], ["Audit chain row", r.auditSeq]]} />
          <p className="muted small">This checks the record against the one row of the audit chain that carries its hash. To verify the whole chain, run <code>python -m smo_shared.audit verify</code>.</p>
        </Card>
      </>}
      {job && r?.jobId && <ConfigJobDrawer id={r.jobId} onClose={() => setJob(false)} />}
      {approval && r?.approvalId && <ApprovalDrawer id={r.approvalId} onClose={() => setApproval(false)} />}
    </>
  );
}
