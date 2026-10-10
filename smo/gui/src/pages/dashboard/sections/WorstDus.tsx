/** "Worst DUs" (`dashboard.worst`, handoff `Main.dc.html`): the managed elements with the most open critical, then major, then any alarms,
 * ranked by RAN NF OAM in one SQL query (`GET /managed-entities/worst?limit=10`, ran-nf-oam/app/fleet.py). Each row links to the element page
 * and to the alarm console filtered on it. An element with no open alarm is never listed, so an empty box means a quiet network. */
import { Link } from "react-router-dom";

import { Card } from "../../../components/ui";
import { formatCount } from "../../../kit/Kpi";
import { QueryState } from "../../../kit/states";
import { useWorstElements, WORST_TOP } from "../data/queries";

/** The ranking card. */
export function WorstDus() {
  const worst = useWorstElements();
  return (
    <Card section="dashboard.worst" title="Worst DUs" sub={`top ${WORST_TOP} by open critical, major, then all alarms`}
      actions={<Link to="/alarms" className="small">Alarms →</Link>}>
      <QueryState q={worst} empty={<p className="muted">No element has an open alarm.</p>}>
        <ol className="list worst-list">
          {(worst.data ?? []).map((w) => (
            <li key={w.managedElementRef}>
              <span className="grow">
                <Link to={`/elements/${encodeURIComponent(w.managedElementRef)}`}>{w.managedElementRef}</Link>
                <span className="xs muted"> {[w.region, w.siteCluster].filter(Boolean).join(" · ")}</span>
              </span>
              {w.critical > 0 && <span className="sev sev-cr" title="open critical alarms">{formatCount(w.critical)}</span>}
              {w.major > 0 && <span className="sev sev-mj" title="open major alarms">{formatCount(w.major)}</span>}
              <Link className="small" to={`/alarms?me=${encodeURIComponent(w.managedElementRef)}`} aria-label={`${w.openAlarms} open alarms on ${w.managedElementRef}`}>
                {formatCount(w.openAlarms)} open
              </Link>
            </li>
          ))}
        </ol>
      </QueryState>
    </Card>
  );
}
