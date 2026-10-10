/** Section `safeguards.stopall`: "Stop all rApp writes" and, for an admin, "Resume all". One server call each (rApp Management
 * `PUT|DELETE /rapp-mgmt/kill-all`, GUI-9.6): the stop stops every live instance the way the per-rApp stop does (an rApp already stopped keeps
 * its first stop and reason) and answers how many it stopped, how many were already stopped and which failed; the resume answers how many it
 * resumed. The confirm dialog shows the live and stopped counts (`GET /rapp-mgmt/kill-all`). The stop is shown to a role allowed the `PUT`
 * (operator), the resume to one allowed the `DELETE` (admin). */
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { useAuth } from "../../../auth/AuthContext";
import { Field, Modal } from "../../../components/ui";
import { invalidateAfter } from "../../../data/keys";
import { Callout } from "../../../kit/Callout";
import { formatCount } from "../../../kit/Kpi";
import { KILL_ALL, resumeAll, stopAll, useKillAllCount, type KillAllResult, type ResumeAllResult } from "../data/queries";

/** Where the flow is. */
type Phase =
  | { stage: "idle" } | { stage: "confirm-stop" } | { stage: "confirm-resume" } | { stage: "running"; what: "stop" | "resume" }
  | { stage: "stopped"; result: KillAllResult } | { stage: "resumed"; result: ResumeAllResult } | { stage: "error"; error: string };

/** The buttons, their confirm dialogs and the result. */
export function GlobalStop() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [phase, setPhase] = useState<Phase>({ stage: "idle" });
  const [reason, setReason] = useState("");
  const counting = phase.stage === "confirm-stop" || phase.stage === "confirm-resume";
  const counts = useKillAllCount(counting);
  const mayStop = can("PUT", KILL_ALL);
  const mayResume = can("DELETE", KILL_ALL);
  if (!mayStop && !mayResume) return null;
  const live = counts.data?.instances ?? null;
  const stopped = counts.data?.stopped ?? null;
  const run = async (what: "stop" | "resume") => {
    setPhase({ stage: "running", what });
    try {
      if (what === "stop") setPhase({ stage: "stopped", result: await stopAll(reason) });
      else setPhase({ stage: "resumed", result: await resumeAll() });
    } catch (e) { setPhase({ stage: "error", error: (e as Error).message }); }
    void invalidateAfter(qc, KILL_ALL, ["ran-nf-oam"]);
  };
  const close = () => setPhase({ stage: "idle" });
  const failed = phase.stage === "stopped" || phase.stage === "resumed" ? phase.result.failed : [];
  return (
    <div data-section="safeguards.stopall" className="stack">
      <div className="row gap">
        {mayStop && <button type="button" className="btn danger" disabled={phase.stage === "running"} onClick={() => setPhase({ stage: "confirm-stop" })}>
          {phase.stage === "running" && phase.what === "stop" ? "Stopping every rApp…" : "Stop all rApp writes"}
        </button>}
        {mayResume && <button type="button" className="btn" disabled={phase.stage === "running"} onClick={() => setPhase({ stage: "confirm-resume" })}>
          {phase.stage === "running" && phase.what === "resume" ? "Resuming…" : "Resume all"}
        </button>}
      </div>
      {phase.stage === "error" && <div className="error-box" role="alert">The call failed: {phase.error}</div>}
      {phase.stage === "stopped" && (
        <Callout tone={failed.length ? "bad" : "warn"} title={`Stopped ${formatCount(phase.result.stopped)} rApp${phase.result.stopped === 1 ? "" : "s"} · ${formatCount(phase.result.alreadyStopped)} already stopped${failed.length ? ` · ${failed.length} failed` : ""}`}
          actions={<button type="button" className="btn small" onClick={close}>Dismiss</button>}>
          {failed.length > 0 ? <FailedList failed={failed} /> : "An admin resumes them all with Resume all, or each one from its row."}
        </Callout>
      )}
      {phase.stage === "resumed" && (
        <Callout tone={failed.length ? "bad" : "info"} title={`Resumed ${formatCount(phase.result.resumed)} rApp${phase.result.resumed === 1 ? "" : "s"}${failed.length ? ` · ${failed.length} failed` : ""}`}
          actions={<button type="button" className="btn small" onClick={close}>Dismiss</button>}>
          {failed.length > 0 && <FailedList failed={failed} />}
        </Callout>
      )}
      {phase.stage === "confirm-stop" && (
        <Modal title="Stop every rApp's writes?" onClose={close}>
          <p>{live === null ? "Counting the rApps…" : <>This stops the writes of <strong>{formatCount(live - (stopped ?? 0))}</strong> running rApp instance{live - (stopped ?? 0) === 1 ? "" : "s"} in one call ({formatCount(stopped)} of {formatCount(live)} are already stopped and keep their first reason).</>}
            {" "}Their config jobs are refused until an admin resumes them; undoing changes still works.</p>
          {counts.error && <p className="small t-warn">Could not count the rApps ({counts.error.message}); the stop still applies to every one.</p>}
          <Field label="Reason" hint="Shown with every stopped rApp and in the refusal events"><input value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} autoFocus /></Field>
          <div className="row gap">
            <button type="button" className="btn danger" onClick={() => void run("stop")}>Stop all rApps</button>
            <button type="button" className="btn ghost" onClick={close}>Cancel</button>
          </div>
        </Modal>
      )}
      {phase.stage === "confirm-resume" && (
        <Modal title="Resume every stopped rApp?" onClose={close}>
          <p>{stopped === null ? "Counting the stopped rApps…" : <>This resumes <strong>{formatCount(stopped)}</strong> stopped rApp instance{stopped === 1 ? "" : "s"}, whoever stopped them and why.</>}</p>
          <div className="row gap">
            <button type="button" className="btn primary" disabled={stopped === 0} onClick={() => void run("resume")}>Resume all</button>
            <button type="button" className="btn ghost" onClick={close}>Cancel</button>
          </div>
        </Modal>
      )}
    </div>
  );
}

/** The instances a global call could not stop or resume, with the error of each. */
function FailedList({ failed }: { failed: { instanceId: string; error: string }[] }) {
  return <ul className="list">{failed.map((f) => <li key={f.instanceId}><code>{f.instanceId}</code><span className="grow small">{f.error}</span></li>)}</ul>;
}
