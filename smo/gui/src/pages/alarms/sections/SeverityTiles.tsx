/** Section `alarms.tiles`: one tile per perceived severity with the true count from the BFF summary (never counted from a list page), the
 * open total, and mean time to acknowledge (a data gap: shown as "—"). A severity tile toggles the RAN table's severity filter. */
import { Card } from "../../../components/ui";
import { formatCount, Kpi } from "../../../kit/Kpi";
import { ErrorRetry } from "../../../kit/states";
import { count, openAlarms } from "../../../data/summary";
import { SEVERITIES } from "../../../lib/domain";
import { useAlarmSummary } from "../data/queries";

/** The tiles; `severity` is the active filter ("" for none), `onSeverity` sets it. */
export function SeverityTiles({ severity, onSeverity }: { severity: string; onSeverity: (s: string) => void }) {
  const summary = useAlarmSummary();
  const s = summary.data;
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
        <Kpi label="Open" value={s ? formatCount(openAlarms(s)) : "…"} foot="every RAN alarm but the cleared ones" />
        <Kpi label="Time to acknowledge" value={null} foot={<span className="gap-note">not measured yet: the backend keeps no ack time</span>} />
      </div>
      {s && s.partial.length > 0 && <p className="small muted">Partial: {s.partial.join(", ")} did not answer; their counts show "—".</p>}
    </Card>
  );
}
