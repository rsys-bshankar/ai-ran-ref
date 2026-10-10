/** The rApp detail "Safeguards" box: whether its writes are stopped, and its limits as meters (config jobs used this hour against the hourly
 * limit; elements per job and change per write as maxima), from `/rapp-mgmt/instances/{id}/safeguards`. Stop and limits are changed in the
 * header (Stop / Resume) and under Safeguards. Section id `rapp.safeguards`. */
import { Link } from "react-router-dom";

import { Card, KeyValue, StateBadge } from "../../../components/ui";
import { UsageMeter } from "../../../kit/Meter";
import { Empty, QueryState } from "../../../kit/states";
import { describeLimits, formatTime } from "../../../lib/domain";
import { useSafeguards } from "../data/queries";

/** The box of instance `id`. */
export function Safeguards({ id }: { id: string }) {
  const q = useSafeguards(id);
  const d = q.data;
  const l = d?.limits;
  return (
    <Card section="rapp.safeguards" title="Safeguards" actions={<Link className="btn small" to="/safeguards">Edit →</Link>}>
      <QueryState q={q} isEmpty={() => false}>
        {d && !d.invokerId ? <Empty title="A terminated instance has no credential, so nothing to stop or limit." /> : d && (
          <div className="stack">
            <KeyValue items={[
              ["Status", d.killed ? <span key="k"><StateBadge state="DISABLED" /> stopped by {d.kill?.killedBy} ({d.kill?.reason ?? "no reason given"}, {formatTime(d.kill?.killedAt)})</span> : <StateBadge key="k" state="ACTIVE" />],
              ["Limits", describeLimits(l)],
            ]} />
            {l?.maxConfigJobsPerHour != null && (
              <div><div className="row between small"><span>Config jobs this hour</span><span className="num">{l.configJobsLastHour} / {l.maxConfigJobsPerHour}</span></div>
                <UsageMeter used={l.configJobsLastHour} limit={l.maxConfigJobsPerHour} label="Config jobs this hour" /></div>
            )}
            {l && <div className="row gap wrap small">
              <span className="chip">Elements per job: {l.maxElementsPerJob ?? "no limit"}</span>
              <span className="chip">Change per write: {l.maxChangePercent != null ? `max ${l.maxChangePercent}%` : "no limit"}</span>
            </div>}
          </div>
        )}
      </QueryState>
      <p className="small"><Link to="/safeguards">Stop or limit this rApp under Safeguards →</Link></p>
    </Card>
  );
}
