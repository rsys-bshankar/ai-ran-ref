/**
 * The drawer of one CM write job at RAN NF OAM (polled every 5 s): its status and wave progress, the decision record when an rApp made it (AI-13.4), the Continue/Halt/Abort
 * buttons of a halted job, the rollback dialog (a preview first, then the rollback, forced only when a value was changed after the job wrote it), the KPI guard settings and result,
 * and the sub-changes. Used by the pages that show config jobs (Infrastructure → O1 jobs, Configuration, Approvals, Decisions, element onboarding). The wording and the wave rules come from `lib/domain.ts`; the buttons are `ActionButton`s,
 * so a user whose role may not make the call does not see them.
 */

import { useState } from "react";
import { Link } from "react-router-dom";

import { useSmo, useSmoAction } from "../api/hooks";
import type { ConfigJob, DecisionRecord, RollbackPreview } from "../api/types";
import { ActionButton, Can, DataTable, Drawer, ErrorBox, Id, KeyValue, Modal, StateBadge } from "./ui";
import { HALT_MEANING, canRollback, describeDifferences, describeGuardResult, formatTime, waveActions, waveProgress } from "../lib/domain";

/** One CM write job: its waves, what an operator may do to it, whether and how to undo it, and what its KPI guard found. */
export function ConfigJobDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const job = useSmo<ConfigJob>(`/ran-nf-oam/config-jobs/${id}`, undefined, { refetchInterval: 5_000 });
  const [rolling, setRolling] = useState(false);
  const why = useSmo<DecisionRecord[]>("/ran-nf-oam/decision-records", { job_id: id, limit: 1 });      // AI-13.4: a job an rApp made has a record of why
  const data = job.data;
  const decision = why.data?.[0];
  const base = `/ran-nf-oam/config-jobs/${id}`;
  return (
    <Drawer title={<>Config job <Id value={id} /></>} onClose={onClose}>
      <ErrorBox error={job.error} />
      {data && <>
        <div className="row gap wrap">
          <StateBadge state={data.status} />
          <span className="muted small">{waveProgress(data)}</span>
          {data.rollbackOf && <span className="small">undoes job <Id value={data.rollbackOf} />{data.rollbackForced ? " (forced over later changes)" : ""}</span>}
        </div>

        {decision && (
          <div className="card" role="note">
            <strong>Made for an rApp</strong>{decision.approvedBy ? <span className="small"> · approved by {decision.approvedBy}</span> : null}
            <p className="small">{decision.rationale ?? <span className="muted">The rApp gave no rationale.</span>}{decision.modelVersion ? <span className="muted"> ({decision.modelVersion})</span> : null}</p>
            <Link to={`/decisions/${decision.decisionId}`}>Why this change →</Link>
          </div>
        )}

        {data.status === "HALTED" && (
          <div className="card" role="status">
            <strong>{HALT_MEANING[data.haltedReason ?? ""] ?? "Halted"}</strong>
            {data.haltedDetail && <p className="small">{data.haltedDetail}</p>}
            {data.haltedReason === "WAVE_PAUSE" && data.nextWaveAt && <p className="muted small">Goes on by itself at {formatTime(data.nextWaveAt)}.</p>}
            <div className="row gap">
              {waveActions(data).map(({ action, force }) => (
                <ActionButton key={action} label={action === "continue" ? (force ? "Continue now" : "Continue") : action === "halt" ? "Halt" : "Abort"}
                  tone={action === "abort" ? "danger" : action === "continue" ? "primary" : "default"}
                  confirm={action === "abort" ? "Abort this job? The waves that have not run are rejected; what was applied stays." : force ? "The pause between waves has not elapsed. Go on now?" : undefined}
                  action={{ method: "POST", path: `${base}/${action}`, json: { force }, success: action === "continue" ? "Next wave started" : action === "halt" ? "Job halted" : "Job aborted" }} />
              ))}
            </div>
          </div>
        )}

        <Can method="POST" path={`${base}/rollback`}>
          {canRollback(data) && <div className="row gap"><button className="btn" onClick={() => setRolling(true)}>Roll back…</button></div>}
        </Can>

        {data.kpiGuard && (
          <div className="card">
            <strong>KPI guard</strong>
            <KeyValue items={[
              ["KPI", data.kpiGuard.kpi],
              ["Checks", `${data.kpiGuard.observationMinutes} min after the job, against the ${data.kpiGuard.baselineMinutes} min before; a ${data.kpiGuard.direction === "higher" ? "drop" : "rise"} of more than ${data.kpiGuard.maxRegressionPercent}% is a regression`],
              ["On regression", data.kpiGuard.revert ? "roll back the regressed elements (never over a later change)" : "report only"],
              ["Result", describeGuardResult(data.kpiGuardResult, data.kpiGuardCheckedAt)],
              ["Checked", data.kpiGuardResult?.checkedAt ? formatTime(data.kpiGuardResult.checkedAt) : null],
              ["Rollback job", data.kpiGuardResult?.revertJobId ? <Id value={data.kpiGuardResult.revertJobId} /> : null],
            ]} />
          </div>
        )}

        <DataTable rows={data.subChanges} rowKey={(s) => `${s.managedElementRef}/${s.managedFunctionRef ?? ""}/${s.operation}`} empty="No sub-changes." columns={[
          { header: "Managed element", render: (s) => <>{s.managedElementRef}{s.managedFunctionRef ? <span className="muted"> / {s.managedFunctionRef}</span> : null}</> },
          ...(data.waveCount && data.waveCount > 1 ? [{ header: "Wave", render: (s: ConfigJob["subChanges"][number]) => s.wave ?? "—" }] : []),
          { header: "Operation", render: (s) => s.operation },
          { header: "Status", render: (s) => <StateBadge state={s.status} /> },
          { header: "Rejection", render: (s) => (s.rejectionReason ? <span title={s.rejectionDetail ?? undefined}>{s.rejectionReason}</span> : "—") },
        ]} />
      </>}
      {rolling && <RollbackDialog id={id} onClose={() => setRolling(false)} />}
    </Drawer>
  );
}

/** Undo a job: first a preview (what would be written, and what has changed since the job wrote it), then the rollback. A value that was changed
 * later is only overwritten when the operator says so, by name. */
/**
 * Dialog behind "Roll back…": the first step is a dry-run POST that only shows what would be written and which values changed after the job wrote them; the second step
 * starts the rollback job. The rollback sends `force: true` only when the preview listed later changes, and the button then says "Roll back anyway", so overwriting someone's later change is never silent.
 */
function RollbackDialog({ id, onClose }: { id: string; onClose: () => void }) {
  const path = `/ran-nf-oam/config-jobs/${id}/rollback`;
  const preview = useSmoAction();
  const run = useSmoAction();
  const [plan, setPlan] = useState<RollbackPreview | null>(null);
  const differences = plan ? describeDifferences(plan) : [];
  return (
    <Modal title="Roll back this job" onClose={onClose}>
      <p className="muted small">Writes the values the job replaced, in reverse order, as a new job that goes through the same access checks as any write.</p>
      {!plan && <button className="btn primary" disabled={preview.isPending}
        onClick={() => preview.mutate({ method: "POST", path, json: { dryRun: true } }, { onSuccess: (d) => setPlan(d as RollbackPreview) })}>
        {preview.isPending ? "…" : "Preview"}
      </button>}
      {plan && <>
        <p>{plan.changes.length} change(s) would be written.</p>
        {differences.length > 0 && (
          <div className="error-box" role="alert">
            <strong>{differences.length} value(s) were changed after this job wrote them:</strong>
            <ul>{differences.map((d) => <li key={d}>{d}</li>)}</ul>
            Rolling back anyway overwrites those later changes.
          </div>
        )}
        <div className="row gap">
          <button className={`btn ${differences.length ? "danger" : "primary"}`} disabled={run.isPending}
            onClick={() => run.mutate({ method: "POST", path, json: { force: differences.length > 0 }, success: "Rollback job started" }, { onSuccess: onClose })}>
            {run.isPending ? "…" : differences.length ? "Roll back anyway" : "Roll back"}
          </button>
          <button className="btn ghost" onClick={onClose}>Cancel</button>
        </div>
      </>}
    </Modal>
  );
}
