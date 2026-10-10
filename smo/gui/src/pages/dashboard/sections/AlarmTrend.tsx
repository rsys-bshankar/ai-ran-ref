/** Open alarms by severity (`dashboard.alarms`, handoff `Main.dc.html` "Alarms raised · 24 h"). ⚠ Data gap (SCALE.md P12): no route counts alarms
 * per hour on the server, and bucketing a list in the browser would need every alarm of the day. So the box shows today's distribution of the
 * open alarms by severity, from the summary's true totals, and says the hourly trend is not served yet. */
import { Link } from "react-router-dom";

import { Card } from "../../../components/ui";
import { count } from "../../../data/summary";
import { formatCount } from "../../../kit/Kpi";
import { Meter } from "../../../kit/Meter";
import { Skeleton } from "../../../kit/states";
import { useDashboardSummary } from "../data/queries";
import { SEVERITY_PARTS } from "./KpiTiles";

/** The card. */
export function AlarmTrend() {
  const summary = useDashboardSummary();
  const s = summary.data;
  return (
    <Card section="dashboard.alarms" title="Open alarms by severity" sub="RAN NF (O1 FaultMnS), counted on the server" actions={<Link to="/alarms" className="small">Alarm console →</Link>}>
      {!s ? <Skeleton lines={3} /> : (
        <>
          <div className="sev-counts">
            {SEVERITY_PARTS.map((p) => (
              <Link key={p.key} to="/alarms" className={`sev-count sev-${p.tone} sev-${p.key}`}><span>{formatCount(count(s, `alarms.${p.key}`))}</span>{p.key}</Link>
            ))}
          </div>
          <Meter thick parts={SEVERITY_PARTS.map((p) => ({ key: p.key, tone: p.tone, value: count(s, `alarms.${p.key}`) ?? 0 }))} />
          <p className="muted small">O-Cloud (FOCOM) alarms: {formatCount(count(s, "ocloudAlarms.total"))}</p>
        </>
      )}
      <p className="gap-note">The hourly trend needs alarm counts per hour from the server; not served yet.</p>
    </Card>
  );
}
