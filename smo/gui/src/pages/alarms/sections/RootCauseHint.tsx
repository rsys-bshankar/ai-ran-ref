/** Section `alarms.rootcause`: a likely-root-cause hint for the selected alarm. The backend has no alarm correlation yet (SCALE.md, Alarms:
 * root-cause hint; `PR-MGT-9`), so this is a labelled heuristic: the other alarms of the same managed element raised within 60 s of it, among the
 * newest 20 alarms of that element (one bounded call). It also shows the alarm's own `rootCauseIndicator` and `correlationGroup` when the element
 * sent them. */
import type { Alarm } from "../../../api/types";
import { Card, SeverityChip } from "../../../components/ui";
import { Callout } from "../../../kit/Callout";
import { Empty, QueryState } from "../../../kit/states";
import { SAME_ELEMENT_LIMIT, SAME_ELEMENT_WINDOW_S, sameElementWithin, useSameElementAlarms } from "../data/queries";

/** The hint box; renders nothing until an alarm is selected. */
export function RootCauseHint({ alarm }: { alarm: Alarm | null }) {
  const near = useSameElementAlarms(alarm?.managedElementRef ?? null);
  if (!alarm) return null;
  const matches = near.data ? sameElementWithin(alarm, near.data.items) : [];
  return (
    <Card section="alarms.rootcause" title="Likely root cause" sub={`Heuristic: same managed element within ${SAME_ELEMENT_WINDOW_S} s`}>
      {alarm.rootCauseIndicator && <Callout tone="warn" title="The element flagged this alarm as a root cause">rootCauseIndicator is set on this alarm.</Callout>}
      <QueryState q={near} isEmpty={() => false}>
        {matches.length === 0
          ? <Empty title={`No other alarm on ${alarm.managedElementRef} within ${SAME_ELEMENT_WINDOW_S} s.`}>This alarm looks like it stands alone.</Empty>
          : <Callout tone="info" title={`${matches.length} other alarm${matches.length === 1 ? "" : "s"} on ${alarm.managedElementRef} within ${SAME_ELEMENT_WINDOW_S} s: possibly one cause`}>
              <ul className="list" aria-label="Alarms raised close together">
                {matches.slice(0, 10).map(({ alarm: o, deltaS }) => (
                  <li key={o.alarmId}>
                    <SeverityChip severity={o.severity} />
                    <span className="grow">{o.probableCause ?? o.specificProblem ?? o.sourceAlarmId}{o.managedFunctionRef && <> · <code className="small">{o.managedFunctionRef}</code></>}</span>
                    <span className="alarms-delta">{deltaS >= 0 ? "+" : "−"}{Math.abs(deltaS)} s</span>
                  </li>
                ))}
              </ul>
            </Callout>}
      </QueryState>
      {alarm.correlationGroup && <p className="small">Correlation group sent by the element: <code>{alarm.correlationGroup}</code></p>}
      <p className="gap-note">Not a server-side correlation: it compares raise times among the newest {SAME_ELEMENT_LIMIT} alarms of this element only.</p>
    </Card>
  );
}
