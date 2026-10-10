/** Lifecycle failure notices (`software.watchers` on the Software page, `configuration.watchers` on Configuration → Element onboarding;
 * PR-MGT-14.7, MGT-15.6): who is told, by a webhook through RAN NF OAM's delivery queue, when an element's onboarding fails, a software
 * campaign halts or a campaign's rollback fails. Lists `GET /ran-nf-oam/lifecycle-subscriptions`; an admin adds (`POST`) and removes
 * (`DELETE /{id}`) a watcher, role-gated with `Can` / `ActionButton` against the BFF's permission table. The event names in
 * `LIFECYCLE_EVENTS` are the ones RAN NF OAM accepts (ran-nf-oam/app/lifecycle.py); a new event there must be added here. The URL is only
 * trimmed here: RAN NF OAM refuses an unusable or internal address and the refusal is shown as a toast. */
import { useState } from "react";

import { useSmo, useSmoAction } from "../../../api/hooks";
import { ActionButton, Can, Card, DataTable, Field, Modal } from "../../../components/ui";
import { formatTime } from "../../../lib/domain";

/** The subscription route. */
export const LIFECYCLE_SUBSCRIPTIONS_PATH = "/ran-nf-oam/lifecycle-subscriptions";

/** The events a watcher can ask for, with what each means. */
export const LIFECYCLE_EVENTS = [
  ["ONBOARDING_FAILED", "An element's onboarding ended FAILED"],
  ["CAMPAIGN_HALTED", "A software campaign halted for a failed gate or by an operator (not the routine pause between waves)"],
  ["CAMPAIGN_ROLLBACK_FAILED", "A campaign's rollback ended with a revert job failed"],
] as const;

/** One watcher as RAN NF OAM lists it; `events` empty means every event. */
interface LifecycleSubscription { subscriptionId: string; callbackUri: string; events: string[]; createdAt: string | null }

/** The watcher card; `section` is the box's id on the page that shows it. */
export function LifecycleWatchers({ section }: { section: string }) {
  const subs = useSmo<LifecycleSubscription[]>(LIFECYCLE_SUBSCRIPTIONS_PATH, { limit: 200 });
  const [adding, setAdding] = useState(false);
  return (
    <Card section={section} title="Who is told about failures" sub="onboarding failed · campaign halted · rollback failed"
      actions={<Can method="POST" path={LIFECYCLE_SUBSCRIPTIONS_PATH}><button type="button" className="btn primary" onClick={() => setAdding(true)}>Add watcher</button></Can>}>
      <p className="muted small">
        A watcher (a webhook) is sent a message when an element's onboarding fails, when a software campaign halts, or when a rollback fails. Messages go through the delivery queue,
        so a watcher that is down misses nothing for long. With no watcher nothing is sent; the state and the alarm are still there.
      </p>
      <DataTable rows={subs.data} rowKey={(s) => s.subscriptionId} error={subs.error} loading={subs.isLoading} empty="Nobody is watching." columns={[
        { header: "Callback", render: (s) => <code className="small">{s.callbackUri}</code> },
        { header: "Events", render: (s) => (s.events.length ? s.events.join(", ") : "all") },
        { header: "Since", render: (s) => formatTime(s.createdAt) },
        { header: "", className: "actions", render: (s) => <ActionButton action={{ method: "DELETE", path: `${LIFECYCLE_SUBSCRIPTIONS_PATH}/${s.subscriptionId}`, success: "Watcher removed" }} label="Remove" tone="danger" confirm="Stop telling this watcher?" /> },
      ]} />
      {adding && <AddWatcher onClose={() => setAdding(false)} />}
    </Card>
  );
}

/**
 * The modal that adds a watcher: a callback URL and the events to be told about (none ticked means all). Posts to the subscriptions route and, on success, closes through `onClose`;
 * the button stays disabled while the URL is blank or a call is pending.
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
        <button type="button" className="btn primary" disabled={action.isPending || !uri.trim()} onClick={() => action.mutate(
          { method: "POST", path: LIFECYCLE_SUBSCRIPTIONS_PATH, json: { callbackUri: uri.trim(), events }, success: "Watcher added" }, { onSuccess: onClose })}>
          {action.isPending ? "…" : "Add watcher"}
        </button>
        <button type="button" className="btn ghost" onClick={onClose}>Cancel</button>
      </div>
    </Modal>
  );
}
