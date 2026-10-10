/** Section `safeguards.stopall`: "Stop all rApp writes". The backend has no global stop (SCALE.md, Safeguards: a server-side global stop is a
 * back-end ask), so this reads every instance that can still write (not UNDEPLOYED), asks to confirm with that count and a reason, then calls
 * the per-rApp stop (`PUT /rapp-mgmt/instances/{id}/kill`) for each, one after another, and shows how many stopped and which failed. Shown only to
 * a role allowed to stop an rApp. */
import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { smo } from "../../../api/client";
import { useAuth } from "../../../auth/AuthContext";
import { Field, Modal } from "../../../components/ui";
import { invalidateAfter } from "../../../data/keys";
import { Callout } from "../../../kit/Callout";
import { fetchStoppableInstances, INSTANCES, killPath } from "../data/queries";

/** Where the stop-all flow is. */
type Phase =
  | { stage: "idle" } | { stage: "counting" } | { stage: "confirm"; ids: string[] } | { stage: "running"; done: number; total: number }
  | { stage: "done"; stopped: number; failed: { id: string; error: string }[] } | { stage: "error"; error: string };

/** The button, its confirm dialog and the result. */
export function GlobalStop() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [phase, setPhase] = useState<Phase>({ stage: "idle" });
  const [reason, setReason] = useState("");
  if (!can("PUT", killPath("any"))) return null;
  const start = async () => {
    setPhase({ stage: "counting" });
    try { setPhase({ stage: "confirm", ids: (await fetchStoppableInstances()).map((i) => i.instanceId) }); }
    catch (e) { setPhase({ stage: "error", error: (e as Error).message }); }
  };
  const run = async (ids: string[]) => {
    const failed: { id: string; error: string }[] = [];
    for (let i = 0; i < ids.length; i++) {
      setPhase({ stage: "running", done: i, total: ids.length });
      try { await smo(killPath(ids[i]), { method: "PUT", json: { reason: reason.trim() || null } }); }
      catch (e) { failed.push({ id: ids[i], error: (e as Error).message }); }
    }
    setPhase({ stage: "done", stopped: ids.length - failed.length, failed });
    void invalidateAfter(qc, INSTANCES, ["ran-nf-oam"]);
  };
  const close = () => setPhase({ stage: "idle" });
  return (
    <div data-section="safeguards.stopall" className="stack">
      <div className="row gap">
        <button type="button" className="btn danger" disabled={phase.stage === "counting" || phase.stage === "running"} onClick={() => void start()}>
          {phase.stage === "counting" ? "Counting rApps…" : phase.stage === "running" ? `Stopping ${phase.done + 1} of ${phase.total}…` : "Stop all rApp writes"}
        </button>
      </div>
      {phase.stage === "error" && <div className="error-box" role="alert">Could not read the rApp instances: {phase.error}</div>}
      {phase.stage === "done" && (
        <Callout tone={phase.failed.length ? "bad" : "warn"} title={`Stopped ${phase.stopped} rApp${phase.stopped === 1 ? "" : "s"}${phase.failed.length ? `; ${phase.failed.length} failed` : ""}`}
          actions={<button type="button" className="btn small" onClick={close}>Dismiss</button>}>
          {phase.failed.length > 0 ? phase.failed.map((f) => <div key={f.id}><code>{f.id}</code>: {f.error}</div>) : "Resume each rApp from its row when it is safe again."}
        </Callout>
      )}
      {phase.stage === "confirm" && (
        <Modal title={`Stop all ${phase.ids.length} rApps?`} onClose={close}>
          <p>This stops the writes of <strong>{phase.ids.length}</strong> rApp instance{phase.ids.length === 1 ? "" : "s"}, one call each. Their config jobs are refused until an admin resumes each one; undoing changes still works. An rApp that is already stopped is stopped again with this reason.</p>
          <Field label="Reason" hint="Shown with every stopped rApp and in the refusal events"><input value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} autoFocus /></Field>
          <div className="row gap">
            <button type="button" className="btn danger" disabled={phase.ids.length === 0} onClick={() => void run(phase.ids)}>Stop {phase.ids.length} rApps</button>
            <button type="button" className="btn ghost" onClick={close}>Cancel</button>
          </div>
        </Modal>
      )}
    </div>
  );
}
