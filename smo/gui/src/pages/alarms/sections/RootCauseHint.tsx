/** Section `alarms.rootcause`: the likely root cause of the selected alarm, as RAN NF OAM correlates it (`GET /alarms/{id}/correlated`, rule
 * "same-element-within-window": the other alarms of the same managed element raised within 60 s before or after it, cleared ones included). The
 * rule is a stated heuristic computed on the server, not a root-cause analysis, and the box names it. It also shows the alarm's own
 * `rootCauseIndicator` and `correlationGroup` when the element sent them. */
import type { Alarm } from "../../../api/types";
import { Card, SeverityChip } from "../../../components/ui";
import { Callout } from "../../../kit/Callout";
import { Empty, QueryState } from "../../../kit/states";
import { SAME_ELEMENT_WINDOW_S, useCorrelated, withDelta } from "../data/queries";

/** The hint box; renders nothing until an alarm is selected. */
export function RootCauseHint({ alarm }: { alarm: Alarm | null }) {
  const correlated = useCorrelated(alarm?.alarmId ?? null);
  if (!alarm) return null;
  const window = correlated.data?.windowSeconds ?? SAME_ELEMENT_WINDOW_S;
  const matches = correlated.data ? withDelta(alarm, correlated.data.items) : [];
  return (
    <Card section="alarms.rootcause" title="Likely root cause" sub={`Server rule: ${correlated.data?.rule ?? "same-element-within-window"}, ±${window} s`}>
      {alarm.rootCauseIndicator && <Callout tone="warn" title="The element flagged this alarm as a root cause">rootCauseIndicator is set on this alarm.</Callout>}
      <QueryState q={correlated} isEmpty={() => false}>
        {matches.length === 0
          ? <Empty title={`No other alarm on ${alarm.managedElementRef} within ${window} s.`}>This alarm looks like it stands alone.</Empty>
          : <Callout tone="info" title={`${matches.length}${correlated.data?.truncated ? "+" : ""} other alarm${matches.length === 1 ? "" : "s"} on ${alarm.managedElementRef} within ${window} s: possibly one cause`}>
              <ul className="list" aria-label="Alarms raised close together">
                {matches.slice(0, 10).map(({ alarm: o, deltaS }) => (
                  <li key={o.alarmId}>
                    <SeverityChip severity={o.severity} />
                    <span className="grow">{o.probableCause ?? o.specificProblem ?? o.sourceAlarmId}{o.managedFunctionRef && <> · <code className="small">{o.managedFunctionRef}</code></>}</span>
                    <span className="alarms-delta">{Number.isNaN(deltaS) ? "—" : `${deltaS >= 0 ? "+" : "−"}${Math.abs(deltaS)} s`}</span>
                  </li>
                ))}
              </ul>
            </Callout>}
      </QueryState>
      {alarm.correlationGroup && <p className="small">Correlation group sent by the element: <code>{alarm.correlationGroup}</code></p>}
    </Card>
  );
}
