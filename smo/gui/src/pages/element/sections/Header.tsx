/** Element detail · header (`element.header`): the element ref, its root DN, vendor and O1 protocol, entity type, region and tenant
 * (`GET /managed-entities/{me}`), with its critical alarm count, and links to its neighbours (RAN topology), a software upgrade and a new
 * config job. An element that is not registered (404) says so. */
import { Link } from "react-router-dom";

import { PageHeader } from "../../../components/ui";
import { Badge } from "../../../kit/Badge";
import { ErrorRetry } from "../../../kit/states";
import { useAlarmCount, useEntity } from "../data/queries";
import { rootDn } from "../data/types";

/** The header of element `me`. */
export function Header({ me }: { me: string }) {
  const entity = useEntity(me);
  const critical = useAlarmCount(me, "critical");
  const e = entity.data;
  const facts = e ? [e.vendorName && `${e.vendorName}${e.o1Protocol ? ` · O1 ${e.o1Protocol}` : ""}`, e.entityType, e.region || e.tenant ? `${e.region ?? "—"} / ${e.tenant ?? "—"}` : null].filter(Boolean) : [];
  const n = critical.data?.total ?? 0;
  return (
    <div data-section="element.header">
      <PageHeader eyebrow="RAN NF OAM · managed element" title={me}
        subtitle={<span className="row wrap"><span className="mono">{rootDn(me)}</span>{facts.map((f) => <span key={String(f)}>· {f}</span>)}
          {n > 0 && <Badge tone="bad">{n} critical</Badge>}</span>}
        actions={<>
          <Link className="btn" to={`/topology?me=${encodeURIComponent(me)}`}>Neighbours</Link>
          <Link className="btn" to="/software#new">Upgrade software</Link>
          <Link className="btn primary" to="/configuration#new">New config job</Link>
        </>} />
      {entity.error && (entity.error.status === 404
        ? <div className="error-box" role="alert">No managed element {me} is registered. Check the ref, or find it on the RAN topology page.</div>
        : <ErrorRetry error={entity.error} onRetry={() => void entity.refetch()} />)}
    </div>
  );
}
