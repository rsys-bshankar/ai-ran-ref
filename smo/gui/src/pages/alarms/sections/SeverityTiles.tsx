/** Section `alarms.tiles`: one tile per perceived severity with the true count from the BFF summary (never counted from a list page), the open
 * total, the unacknowledged count, the mean time to acknowledge over the alarms acknowledged in the last 24 h (summary `alarms.mtta`, from RAN NF
 * OAM's `/alarms/stats`), and a 24 h sparkline of alarms raised per hour (`/alarms/counts?group_by=hour`). A severity tile toggles the RAN
 * table's severity filter. */
import { Sparkline } from "../../../components/charts";
import { Card } from "../../../components/ui";
import { formatCount, Kpi } from "../../../kit/Kpi";
import { ErrorRetry } from "../../../kit/states";
import { count, openAlarms } from "../../../data/summary";
import { SEVERITIES } from "../../../lib/domain";
import { formatDuration, useAlarmHours, useAlarmSummary } from "../data/queries";

/** The tiles; `severity` is the active filter ("" for none), `onSeverity` sets it. */
export function SeverityTiles({ severity, onSeverity }: { severity: string; onSeverity: (s: string) => void }) {
  const summary = useAlarmSummary();
  const hours = useAlarmHours();
  const s = summary.data;
  const mtta = count(s, "alarms.mtta");
  const raised = hours.data?.groups ?? [];
  return (
    <Card section="alarms.tiles">
      {summary.error && !s && <ErrorRetry error={summary.error} onRetry={() => void summary.refetch()} />}
      <div className="tiles">
        {SEVERITIES.map((sev) => {
          const n = count(s, `alarms.${sev}`);
          return (
            <Kpi key={sev} label={<span className={`sev sev-${sev} alarms-sev-tag`}>{sev}</span>}
              value={s ? formatCount(n) : "…"} foot={severity === sev ? "filtering the table · click to clear" : "click to filter"}
              tone={sev === "critical" && (n ?? 0) > 0 ? "hot" : sev === "major" && (n ?? 0) > 0 ? "warm" : undefined}
              active={severity === sev} onClick={() => onSeverity(severity === sev ? "" : sev)} title={`Show only ${sev} alarms`} />
          );
        })}
        <Kpi label="Open" value={s ? formatCount(openAlarms(s)) : "…"} foot={`${formatCount(count(s, "alarms.unacked"))} unacknowledged`} />
        <Kpi label="Time to acknowledge" value={s ? formatDuration(mtta) : "…"}
          foot={mtta === null ? "no alarm acknowledged in 24 h" : "mean over the alarms acknowledged in 24 h"} />
        <Kpi label="Raised · 24 h" value={hours.data ? formatCount(raised.reduce((a, h) => a + h.count, 0)) : "…"} foot="per hour, UTC">
          {raised.length > 0 && <Sparkline width={140} height={32} label="alarms raised per hour" points={raised.map((h) => ({ t: h.key, v: h.count }))} />}
        </Kpi>
      </div>
      {s && s.partial.length > 0 && <p className="small muted">Partial: {s.partial.join(", ")} did not answer; their counts show "—".</p>}
    </Card>
  );
}
