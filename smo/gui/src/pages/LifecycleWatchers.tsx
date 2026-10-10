/**
 * The "Who is told about failures" card (PR-MGT-14.7, MGT-15.6), shown under both the onboarding and the campaign tab: the list of lifecycle watchers (webhooks) and the form
 * that adds one. It reads and writes /ran-nf-oam/lifecycle-subscriptions through the GUI backend; the Add and Remove buttons appear only to a role that may POST and DELETE there. The
 * event names in `LIFECYCLE_EVENTS` are the ones RAN NF OAM accepts (app/lifecycle.py); a new event there must be added here. Mounted by pages/Onboarding.tsx and pages/Campaigns.tsx.
 */
import { useState } from "react";

import { useSmo, useSmoAction } from "../api/hooks";
import { ActionButton, Can, Card, DataTable, Field, Modal } from "../components/ui";
import { formatTime } from "../lib/domain";

const SUBSCRIPTIONS = "/ran-nf-oam/lifecycle-subscriptions";
export const LIFECYCLE_EVENTS = [
  ["ONBOARDING_FAILED", "An element's onboarding ended FAILED"],
  ["CAMPAIGN_HALTED", "A software campaign halted for a failed gate or by an operator (not the routine pause between waves)"],
  ["CAMPAIGN_ROLLBACK_FAILED", "A campaign's rollback ended with a revert job failed"],
] as const;

interface LifecycleSubscription { subscriptionId: string; callbackUri: string; events: string[]; createdAt: string | null }

/** Who is told when an onboarding fails, a campaign halts or a rollback fails (PR-MGT-14.7, MGT-15.6): a webhook, through the delivery queue. Admins add and remove. */
export function LifecycleWatchers() {
  const subs = useSmo<LifecycleSubscription[]>(SUBSCRIPTIONS, { limit: 200 });
  const [adding, setAdding] = useState(false);
  return (
    <Card title="Who is told about failures" actions={<Can method="POST" path={SUBSCRIPTIONS}><button className="btn primary" onClick={() => setAdding(true)}>Add watcher</button></Can>}>
      <p className="muted small">
        A watcher (a webhook) is sent a message when an element's onboarding fails, when a software campaign halts, or when a rollback fails. Messages go through the delivery queue,
        so a watcher that is down misses nothing for long. With no watcher nothing is sent; the state and the alarm are still there.
      </p>
      <DataTable rows={subs.data} rowKey={(s) => s.subscriptionId} error={subs.error} loading={subs.isLoading} empty="Nobody is watching." columns={[
        { header: "Callback", render: (s) => <code className="small">{s.callbackUri}</code> },
        { header: "Events", render: (s) => (s.events.length ? s.events.join(", ") : "all") },
        { header: "Since", render: (s) => formatTime(s.createdAt) },
        { header: "", render: (s) => <ActionButton action={{ method: "DELETE", path: `${SUBSCRIPTIONS}/${s.subscriptionId}`, success: "Watcher removed" }} label="Remove" tone="danger" confirm="Stop telling this watcher?" /> },
      ]} />
      {adding && <AddWatcher onClose={() => setAdding(false)} />}
    </Card>
  );
}

/**
 * The modal that adds a watcher: a callback URL and the events to be told about (none ticked means all). Posts to the subscriptions route and, on success, closes through `onClose`;
 * the button stays disabled while the URL is blank or a call is pending. The URL is only trimmed here: RAN NF OAM refuses an unusable or internal address and the refusal is shown as a toast.
 */
function AddWatcher({ onClose }: { onClose: () => void }) {
  const [uri, setUri] = useState("");
  const [events, setEvents] = useState<string[]>([]);
  const action = useSmoAction();
  const toggle = (e: string) => setEvents((cur) => (cur.includes(e) ? cur.filter((x) => x !== e) : [...cur, e]));
  return (
    <Modal title="Add a watcher" onClose={onClose}>
      <Field label="Callback URL" hint="Refused if it points at a private or internal address"><input value={uri} onChange={(e) => setUri(e.target.value)} placeholder="https://" autoFocus /></Field>
      <fieldset className="field">
        <legend className="field-label">Which events (none ticked: all)</legend>
        {LIFECYCLE_EVENTS.map(([event, meaning]) => (
          <label key={event} className="row gap small"><input type="checkbox" checked={events.includes(event)} onChange={() => toggle(event)} /> {event} <span className="muted">{meaning}</span></label>
        ))}
      </fieldset>
      <div className="row gap">
        <button className="btn primary" disabled={action.isPending || !uri.trim()} onClick={() => action.mutate(
          { method: "POST", path: SUBSCRIPTIONS, json: { callbackUri: uri.trim(), events }, success: "Watcher added" }, { onSuccess: onClose })}>
          {action.isPending ? "…" : "Add watcher"}
        </button>
        <button className="btn ghost" onClick={onClose}>Cancel</button>
      </div>
    </Modal>
  );
}
