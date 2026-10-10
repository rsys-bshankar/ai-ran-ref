/** The rApp detail header (handoff `RappDetail.dc.html`): name and version, owner, instance id, state and autonomy, the autonomy mode as a
 * read-only segmented control (rApp Management has no route that changes the mode of a running instance: it is fixed at CreateInstance), Pin
 * (`/api/me/pins`) and Stop rApp (with a reason, confirm dialog) / Resume — the same safeguard calls the Safeguards page makes
 * (`PUT|DELETE /rapp-mgmt/instances/{id}/kill`). scripts/gui_rapp_pages_e2e.py reads the pin button's "☆ Pin" / "★ Pinned" wording. */
import { useState } from "react";
import { Link } from "react-router-dom";

import { useSmoAction } from "../../../api/hooks";
import { MAX_PINS, usePinToggle, usePins, type RappPage } from "../../../api/rapps";
import { ActionButton, Can, Field, Id, Modal, PageHeader, StateBadge } from "../../../components/ui";
import { Segmented } from "../../../kit/Segmented";
import { instanceBase } from "../../rapps/data/queries";
import { useSafeguards } from "../data/queries";

/** The three autonomy modes, in order of how much the rApp may do alone. */
const MODES = [{ id: "SHADOW", label: "Shadow" }, { id: "ASSIST", label: "Assist" }, { id: "AUTONOMOUS", label: "Autonomous" }];

/** The page header of rApp `data`. */
export function Header({ data }: { data: RappPage }) {
  const pins = usePins();
  const toggle = usePinToggle();
  const safeguards = useSafeguards(data.instanceId);
  const [stopping, setStopping] = useState(false);
  const pinnedCount = pins.data?.items.length ?? 0;
  const sg = safeguards.data;
  const killPath = `${instanceBase(data.instanceId)}/kill`;
  return (
    <>
      <PageHeader eyebrow={<Link to="/rapps">rApps</Link>} title={`${data.name ?? "rApp"} ${data.version ?? ""}`.trim()}
        subtitle={<>{data.vendor ?? "unknown owner"} · <Id value={data.instanceId} /> · <StateBadge state={data.state} /> · <StateBadge state={data.autonomyMode} />
          {sg?.killed && <> · <StateBadge state="DISABLED" /> writes stopped</>}</>}
        actions={<>
          <Segmented label="Autonomy mode (fixed when the instance was created)" options={MODES.map((m) => ({ ...m, title: "Fixed at CreateInstance: no backend route changes it" }))}
            value={data.autonomyMode ?? ""} onChange={() => {}} disabled />
          <button type="button" className="btn" aria-pressed={data.pinned} disabled={toggle.isPending || (!data.pinned && pinnedCount >= MAX_PINS)}
            title={data.pinned ? "Remove from the sidebar" : pinnedCount >= MAX_PINS ? `At most ${MAX_PINS} pinned: unpin one first` : "Show in the sidebar"}
            onClick={() => toggle.mutate({ instanceId: data.instanceId, pin: !data.pinned })}>{data.pinned ? "★ Pinned" : "☆ Pin"}</button>
          {sg?.invokerId && (sg.killed
            ? <ActionButton label="Resume" confirm="Let this rApp start config jobs again?" action={{ method: "DELETE", path: killPath, success: "rApp may write again" }} />
            : <Can method="PUT" path={killPath}><button type="button" className="btn danger" onClick={() => setStopping(true)}>Stop rApp</button></Can>)}
          <Link className="btn" to="/rapps">← Directory</Link>
        </>} />
      <p className="gap-note">The autonomy mode is fixed when the instance is created; rApp Management has no route to change it on a running instance.</p>
      {stopping && <StopDialog name={data.name ?? "this rApp"} path={killPath} onClose={() => setStopping(false)} />}
    </>
  );
}

/** "Stop <rApp>?" with a reason: its writes are refused until resumed; the instance keeps running. */
function StopDialog({ name, path, onClose }: { name: string; path: string; onClose: () => void }) {
  const [reason, setReason] = useState("");
  const action = useSmoAction();
  return (
    <Modal title={`Stop ${name}?`} onClose={onClose}>
      <p className="muted small">Every write from this rApp is refused until you resume it: its config jobs (dry runs too) at once, every other change through the gateway within a few seconds. It can still read, withdraw what it made and undo its own config jobs. The rApp keeps running and reporting.</p>
      <Field label="Reason" hint="Recorded with the stop and shown in the refusal events">
        <input value={reason} maxLength={500} onChange={(e) => setReason(e.target.value)} autoFocus />
      </Field>
      <div className="row gap end">
        <button type="button" className="btn ghost" onClick={onClose}>Cancel</button>
        <button type="button" className="btn danger" disabled={action.isPending}
          onClick={() => action.mutate({ method: "PUT", path, json: { reason: reason.trim() || null }, success: "rApp stopped" }, { onSuccess: onClose })}>
          {action.isPending ? "…" : "Stop writes"}
        </button>
      </div>
    </Modal>
  );
}
