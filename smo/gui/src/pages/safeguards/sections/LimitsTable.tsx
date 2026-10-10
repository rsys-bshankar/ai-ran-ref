/** Section `safeguards.limits` (tab "Limits"): one row per rApp instance (SCALE.md: a table, not 500 cards), paged on the server. Each row reads
 * the instance's safeguards (one call per row of the page, shared with the detail card): whether it may write, its change-per-write limit, jobs
 * per hour used of the maximum (`UsageMeter`), elements per job, whether its changes are held for approval, and the actions — Stop / Resume,
 * Limits…, Approval… / Stop holding, Remove limits — each shown only to a role allowed to make the call. A row click selects it. */
import { useState } from "react";

import type { InstanceSummary } from "../../../api/types";
import { ActionButton, Can, Card, Id, StateBadge } from "../../../components/ui";
import { UsageMeter } from "../../../kit/Meter";
import { ServerTable } from "../../../kit/ServerTable";
import { describeApprovalPolicy, formatTime } from "../../../lib/domain";
import { approvalPolicyPath, INSTANCES, killPath, limitsPath, useInstanceSafeguards } from "../data/queries";
import { ApprovalPolicyDialog, LimitsDialog, StopDialog } from "./Dialogs";

/** The table; `selectedId` is highlighted, `onSelect` picks a row for the detail card. */
export function LimitsTable({ selectedId, onSelect }: { selectedId: string | null; onSelect: (id: string) => void }) {
  return (
    <Card section="safeguards.limits" title="Limits per rApp">
      <p className="muted small">
        A stopped rApp's config jobs are refused until an admin resumes it; undoing changes (rollback, revert, halt, abort) always works.
        Limits cap how many config jobs it may start per hour, how many managed elements one job may touch, and how far a value may move in one write.
      </p>
      <ServerTable<InstanceSummary> path={INSTANCES} rowKey={(i) => i.instanceId} onRowClick={(i) => onSelect(i.instanceId)} selectedKey={selectedId}
        empty="No rApp instances yet." columns={[
          { header: "Instance", render: (i) => <><Id value={i.instanceId} /> <StateBadge state={i.state} /></> },
          { header: "Mode", render: (i) => <StateBadge state={i.autonomyMode} /> },
          { header: "Writes", render: (i) => <Writes id={i.instanceId} /> },
          { header: "Change / write", render: (i) => <Limit id={i.instanceId} pick="change" /> },
          { header: "Jobs / hour used", render: (i) => <Limit id={i.instanceId} pick="jobs" /> },
          { header: "Elements / job", render: (i) => <Limit id={i.instanceId} pick="elements" /> },
          { header: "Hold", render: (i) => <Hold id={i.instanceId} /> },
          { header: "", className: "actions", render: (i) => <RowActions id={i.instanceId} /> },
        ]} />
    </Card>
  );
}

/** Whether the instance may write. */
function Writes({ id }: { id: string }) {
  const sg = useInstanceSafeguards(id);
  const view = sg.data;
  if (sg.error) return <span className="muted" title={String(sg.error)}>unknown</span>;
  if (!view) return <span className="muted">…</span>;
  if (!view.invokerId) return <span className="muted" title="A terminated instance has no credential, so nothing to stop or limit">no credential</span>;
  if (view.killed) return <span title={`${view.kill?.killedBy ?? ""}: ${view.kill?.reason ?? "no reason given"} (${formatTime(view.kill?.killedAt)})`}><StateBadge state="DISABLED" /> stopped</span>;
  return <StateBadge state="ACTIVE" />;
}

/** One limit of the instance; jobs per hour as a used-of-max meter. */
function Limit({ id, pick }: { id: string; pick: "change" | "jobs" | "elements" }) {
  const l = useInstanceSafeguards(id).data?.limits;
  if (!l) return <span className="muted">—</span>;
  if (pick === "change") return l.maxChangePercent != null ? <span className="num">≤ {l.maxChangePercent} %</span> : <span className="muted">—</span>;
  if (pick === "elements") return l.maxElementsPerJob != null ? <span className="num">≤ {l.maxElementsPerJob}</span> : <span className="muted">—</span>;
  if (l.maxConfigJobsPerHour == null) return <span className="num">{l.configJobsLastHour} <span className="muted small">no max</span></span>;
  return (
    <div className="col safeguards-usage">
      <span className="num small">{l.configJobsLastHour} / {l.maxConfigJobsPerHour}</span>
      <UsageMeter used={l.configJobsLastHour} limit={l.maxConfigJobsPerHour} label={`${l.configJobsLastHour} of ${l.maxConfigJobsPerHour} config jobs this hour`} />
    </div>
  );
}

/** Whether the instance's changes wait for approval. */
function Hold({ id }: { id: string }) {
  const view = useInstanceSafeguards(id).data;
  if (!view?.invokerId) return <span className="muted">—</span>;
  return <span className="small">{describeApprovalPolicy(view.approvalPolicy)}</span>;
}

/** The row's actions and their dialogs. */
function RowActions({ id }: { id: string }) {
  const view = useInstanceSafeguards(id).data;
  const [dialog, setDialog] = useState<"stop" | "limits" | "approval" | null>(null);
  const invoker = view?.invokerId ?? null;
  if (!view || !invoker) return null;
  return (
    <div className="row gap wrap end" onClick={(e) => e.stopPropagation()}>
      {view.killed
        ? <ActionButton action={{ method: "DELETE", path: killPath(id), success: "rApp may write again", invalidates: ["ran-nf-oam"] }} label="Resume" confirm="Let this rApp start config jobs again?" />
        : <Can method="PUT" path={killPath(id)}><button type="button" className="btn danger" onClick={() => setDialog("stop")}>Stop</button></Can>}
      <Can method="PUT" path={limitsPath(invoker)}><button type="button" className="btn" onClick={() => setDialog("limits")}>Limits…</button></Can>
      <Can method="PUT" path={approvalPolicyPath(invoker)}><button type="button" className="btn" onClick={() => setDialog("approval")}>Approval…</button></Can>
      {view.approvalPolicy && <ActionButton action={{ method: "DELETE", path: approvalPolicyPath(invoker), success: "This rApp's changes are written at once again", invalidates: ["rapp-mgmt"] }}
        label="Stop holding" tone="danger" confirm="Write this rApp's changes at once again? Requests already waiting stay in the Approvals inbox." />}
      {view.limits && <ActionButton action={{ method: "DELETE", path: limitsPath(invoker), success: "Limits removed", invalidates: ["rapp-mgmt"] }}
        label="Remove limits" tone="danger" confirm="Remove every limit of this rApp?" />}
      {dialog === "stop" && <StopDialog instanceId={id} onClose={() => setDialog(null)} />}
      {dialog === "limits" && <LimitsDialog invokerId={invoker} current={view.limits} onClose={() => setDialog(null)} />}
      {dialog === "approval" && <ApprovalPolicyDialog invokerId={invoker} current={view.approvalPolicy ?? null} onClose={() => setDialog(null)} />}
    </div>
  );
}
