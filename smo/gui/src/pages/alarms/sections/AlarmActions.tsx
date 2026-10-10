/** Ack / Unack / Clear of one RAN alarm, shared by the alarm table rows (`alarms.table`) and the detail panel (`alarms.detail`). Each is an
 * `ActionButton`, so a role that may not make the call sees no button; the BFF records the signed-in user as the acker or clearer. */
import { ActionButton } from "../../../components/ui";
import type { Alarm } from "../../../api/types";
import { alarmPath } from "../data/queries";

/** The buttons for one alarm. */
export function AlarmActions({ alarm }: { alarm: Alarm }) {
  const base = alarmPath(alarm.alarmId);
  const cleared = alarm.severity === "cleared";
  return (
    <div className="row gap end">
      {alarm.ackState === "UNACKNOWLEDGED"
        ? <ActionButton label="Ack" action={{ method: "PATCH", path: `${base}/ack`, query: { new_state: "ACKNOWLEDGED" }, success: "Alarm acknowledged" }} />
        : <ActionButton label="Unack" action={{ method: "PATCH", path: `${base}/ack`, query: { new_state: "UNACKNOWLEDGED" }, success: "Alarm unacknowledged" }} />}
      {!cleared && <ActionButton label="Clear" tone="primary" action={{ method: "PATCH", path: `${base}/clear`, success: "Alarm cleared" }} />}
    </div>
  );
}
