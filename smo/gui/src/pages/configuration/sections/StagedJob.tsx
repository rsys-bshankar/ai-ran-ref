/** Configuration · staged job detail (`configuration.job`): the selected config job (`GET /config-jobs/{id}`, every 5 s): its waves
 * (currentWave of waveCount, each wave's outcome), the pause countdown, why it halted (`haltedReason` and detail), the controls (Continue now,
 * Halt, Abort through `waveActions`, Roll back with a preview), the KPI guard, and the sub-changes by status with the first 50 listed.
 * "Open job" opens the existing `ConfigJobDrawer` (decision record, full sub-change table). */
import { useState } from "react";

import { ActionButton, Card, DataTable, Id, StateBadge } from "../../../components/ui";
import { ConfigJobDrawer } from "../../../components/ConfigJobDrawer";
import { Badge } from "../../../kit/Badge";
import { Callout } from "../../../kit/Callout";
import { Empty, ErrorRetry, Skeleton } from "../../../kit/states";
import { canRollback, describeSeconds, HALT_MEANING, waveActions, waveProgress } from "../../../lib/domain";
import { Countdown } from "../../software/sections/Countdown";
import { jobPath, useJob, useSelectedJob } from "../data/queries";
import { jobWaveState, subChangeCounts } from "../data/types";
import { JobRollback } from "./JobRollback";
import { KpiGuard } from "./KpiGuard";

/** The detail card of the selected job. */
export function StagedJob() {
  const [id] = useSelectedJob();
  const job = useJob(id);
  const [drawer, setDrawer] = useState(false);
  if (!id) return <Card section="configuration.job" title="Job"><Empty title="Pick a job to see its waves and controls." /></Card>;
  if (job.error && !job.data) return <Card section="configuration.job" title="Job"><ErrorRetry error={job.error} onRetry={() => void job.refetch()} /></Card>;
  const j = job.data;
  if (!j) return <Card section="configuration.job" title="Job"><Skeleton lines={8} /></Card>;
  const waves = Math.max(1, j.waveCount ?? 1);
  const counts = subChangeCounts(j.subChanges);
  const firstRejected = j.subChanges.find((s) => s.status === "REJECTED" && s.rejectionReason !== "WAVE_NOT_RUN");
  return (
    <Card section="configuration.job" title={<span className="row wrap"><Id value={j.jobId} /><StateBadge state={j.status} />{j.haltedReason && <Badge tone="mute" plain>{j.haltedReason}</Badge>}</span>}
      sub={[j.requestedBy && `requested by ${j.requestedBy}`, waveProgress(j), j.waveSize && `wave size ${j.waveSize}`, j.wavePauseSeconds ? `pause ${describeSeconds(j.wavePauseSeconds)}` : null,
        waves > 1 ? `gate ≤ ${j.gateMaxNewAlarms ?? 0} new alarms · on gate failure: ${j.onGateFailure}` : null].filter(Boolean).join(" · ")}
      actions={<button type="button" className="btn small" onClick={() => setDrawer(true)}>Open job</button>}>
      {j.rollbackOf && <p className="small">Undoes job <Id value={j.rollbackOf} />{j.rollbackForced ? " (forced over later changes)" : ""}</p>}
      <div className="eyebrow">Waves · {Math.min(j.currentWave ?? 0, waves)} of {waves}</div>
      <div className="cfg-waves" role="list" aria-label="Waves">
        {Array.from({ length: waves }, (_, i) => i + 1).map((w) => {
          const state = jobWaveState(j, w);
          return <div key={w} role="listitem" className={`cfg-wave ${state}`}><span className="mono xs">W{w}</span><span className="small">{state}</span></div>;
        })}
      </div>
      {j.status === "HALTED" && (
        <Callout tone={j.haltedReason === "WAVE_PAUSE" ? "warn" : "bad"} title={`${j.haltedReason ?? "HALTED"} · ${HALT_MEANING[j.haltedReason ?? ""] ?? "Halted"}`}>
          {j.haltedReason === "WAVE_PAUSE" && j.nextWaveAt ? <>Wave {(j.currentWave ?? 0) + 1} starts in <Countdown until={j.nextWaveAt} /> unless you act.</> : j.haltedDetail}
        </Callout>
      )}
      <div className="row wrap">
        {waveActions(j).map(({ action, force }) => (
          <ActionButton key={action} label={action === "continue" ? (force ? "Continue now" : "Continue") : action === "halt" ? "Halt" : "Abort"}
            tone={action === "abort" ? "danger" : action === "continue" ? "primary" : "default"}
            confirm={action === "abort" ? "Abort this job? The waves that have not run are rejected; what was applied stays." : force ? "The pause between waves has not elapsed. Go on now?" : undefined}
            action={{ method: "POST", path: jobPath(j.jobId, action), json: { force }, success: action === "continue" ? "Next wave started" : action === "halt" ? "Job halted" : "Job aborted" }} />
        ))}
        {canRollback(j) && <JobRollback jobId={j.jobId} label="Roll back applied waves" />}
      </div>
      <div className="eyebrow">KPI guard</div>
      <KpiGuard job={j} />
      <div className="eyebrow">Sub-changes</div>
      <dl className="kv">
        <dt>Applied</dt><dd>{counts.APPLIED ?? 0} of {j.subChanges.length}</dd>
        <dt>Rejected</dt><dd className={counts.REJECTED ? "t-bad" : undefined}>{counts.REJECTED ?? 0}{firstRejected ? ` · ${firstRejected.managedFunctionRef ?? firstRejected.managedElementRef}: ${firstRejected.rejectionReason}` : ""}</dd>
        <dt>Not started</dt><dd>{counts.PENDING ?? 0}</dd>
        {counts.REVERTED ? <><dt>Reverted</dt><dd className="t-warn">{counts.REVERTED}</dd></> : null}
      </dl>
      <DataTable rows={j.subChanges.slice(0, 50)} rowKey={(s) => `${s.managedElementRef}/${s.managedFunctionRef ?? ""}/${s.operation}/${s.wave ?? 1}`} empty="No sub-changes." columns={[
        { header: "Element", render: (s) => <>{s.managedElementRef}{s.managedFunctionRef ? <span className="muted"> / {s.managedFunctionRef}</span> : null}</> },
        { header: "Wave", render: (s) => s.wave ?? 1 },
        { header: "Operation", render: (s) => s.operation },
        { header: "Status", render: (s) => <StateBadge state={s.status} /> },
        { header: "Rejection", render: (s) => (s.rejectionReason ? <span title={s.rejectionDetail ?? undefined}>{s.rejectionReason}</span> : "—") },
      ]} />
      {j.subChanges.length > 50 && <p className="muted small">First 50 of {j.subChanges.length}: “Open job” lists them all.</p>}
      {drawer && <ConfigJobDrawer id={j.jobId} onClose={() => setDrawer(false)} />}
    </Card>
  );
}
