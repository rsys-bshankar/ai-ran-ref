/** Section `safeguards.watchers` (tab "Watchers"): who is told about refusals — webhooks posted through the delivery queue — with Add (admin) and
 * Remove. The list is a server table. */
import { useState } from "react";

import { useSmoAction } from "../../../api/hooks";
import type { RefusalCode, SafeguardSubscription } from "../../../api/types";
import { ActionButton, Can, Card, Field, Modal } from "../../../components/ui";
import { ServerTable } from "../../../kit/ServerTable";
import { formatTime, REFUSAL_CODES, REFUSAL_MEANING } from "../../../lib/domain";
import { WATCHERS } from "../data/queries";

/** The watcher list. */
export function Watchers() {
  const [adding, setAdding] = useState(false);
  return (
    <Card section="safeguards.watchers" title="Who is told about refusals" actions={
      <Can method="POST" path={WATCHERS}><button type="button" className="btn primary" onClick={() => setAdding(true)}>Add watcher</button></Can>}>
      <p className="muted small">Each refusal is posted to every watcher (a webhook), through the delivery queue, so a watcher that is down misses nothing for long.</p>
      <ServerTable<SafeguardSubscription> path={WATCHERS} rowKey={(s) => s.subscriptionId} empty="Nobody is watching." columns={[
        { header: "Callback", render: (s) => <code className="small">{s.callbackUri}</code> },
        { header: "Refusals", render: (s) => (s.refusals.length ? s.refusals.join(", ") : "all") },
        { header: "Since", render: (s) => formatTime(s.createdAt) },
        { header: "", render: (s) => <ActionButton action={{ method: "DELETE", path: `${WATCHERS}/${s.subscriptionId}`, success: "Watcher removed" }}
            label="Remove" tone="danger" confirm="Stop telling this watcher?" /> },
      ]} />
      {adding && <AddWatcher onClose={() => setAdding(false)} />}
    </Card>
  );
}

/** The add-watcher dialog: a callback URL and the refusal codes it wants (none: all). */
function AddWatcher({ onClose }: { onClose: () => void }) {
  const [uri, setUri] = useState("");
  const [codes, setCodes] = useState<RefusalCode[]>([]);
  const action = useSmoAction();
  const toggle = (c: RefusalCode) => setCodes((cur) => (cur.includes(c) ? cur.filter((x) => x !== c) : [...cur, c]));
  return (
    <Modal title="Add a watcher" onClose={onClose}>
      <Field label="Callback URL" hint="Refused if it points at a private or internal address"><input value={uri} onChange={(e) => setUri(e.target.value)} placeholder="https://" autoFocus /></Field>
      <fieldset className="field">
        <legend className="field-label">Which refusals (none ticked: all)</legend>
        {REFUSAL_CODES.map((c) => (
          <label key={c} className="row gap small"><input type="checkbox" checked={codes.includes(c)} onChange={() => toggle(c)} /> {c} <span className="muted">{REFUSAL_MEANING[c]}</span></label>
        ))}
      </fieldset>
      <div className="row gap">
        <button type="button" className="btn primary" disabled={action.isPending || !uri.trim()} onClick={() => action.mutate(
          { method: "POST", path: WATCHERS, json: { callbackUri: uri.trim(), refusals: codes }, success: "Watcher added" }, { onSuccess: onClose })}>
          {action.isPending ? "…" : "Add watcher"}
        </button>
        <button type="button" className="btn ghost" onClick={onClose}>Cancel</button>
      </div>
    </Modal>
  );
}
