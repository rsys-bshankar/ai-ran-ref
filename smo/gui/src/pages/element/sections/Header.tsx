/** Element detail · header (`element.header`): the element ref, its root DN, vendor and O1 protocol, entity type, region and tenant, and its
 * site cluster (`GET /managed-entities/{me}`), with its critical alarm count, and links to its neighbours (RAN topology), a software upgrade and a
 * new config job. An admin edits the site cluster in place (`PUT /managed-entities/{me}/site-cluster`; empty clears it). An element that is not
 * registered (404) says so. */
import { useState } from "react";
import { Link } from "react-router-dom";

import { ActionButton, Can, PageHeader } from "../../../components/ui";
import { Badge } from "../../../kit/Badge";
import { ErrorRetry } from "../../../kit/states";
import { siteClusterPath, useAlarmCount, useEntity } from "../data/queries";
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
          {e && <SiteCluster me={me} value={e.siteCluster ?? null} />}
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

/** The site cluster, and for an admin an inline editor (letters, digits and `. _ : / @ + -`, at most 100; empty clears it). */
function SiteCluster({ me, value }: { me: string; value: string | null }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(value ?? "");
  const valid = text.trim() === "" || /^[A-Za-z0-9._:/@+-]{1,100}$/.test(text.trim());
  if (editing) {
    return (
      <span className="row gap" data-part="site-cluster">
        · <input aria-label="Site cluster" value={text} onChange={(e) => setText(e.target.value)} placeholder="metro-a" maxLength={100} />
        <ActionButton label="Save" tone="primary" disabled={!valid}
          action={{ method: "PUT", path: siteClusterPath(me), json: { siteCluster: text.trim() || null }, success: text.trim() ? `Site cluster set to ${text.trim()}` : "Site cluster cleared" }}
          onDone={() => setEditing(false)} />
        <button type="button" className="btn ghost small" onClick={() => { setText(value ?? ""); setEditing(false); }}>Cancel</button>
      </span>
    );
  }
  return (
    <span className="row gap" data-part="site-cluster">· site cluster {value ?? <span className="muted">none</span>}
      <Can method="PUT" path={siteClusterPath(me)}><button type="button" className="btn ghost small" onClick={() => { setText(value ?? ""); setEditing(true); }}>Edit cluster</button></Can>
    </span>
  );
}
