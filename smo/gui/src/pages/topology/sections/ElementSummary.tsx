/** RAN topology · element summary (`topology.element`): the focused managed element at a glance (vendor, O1 protocol, region and tenant,
 * its cells and their guard classes, sector groups and incident zones), from `GET /managed-entities/{me}`, with links to its Element
 * detail page, its alarms and its cell guards. */
import { Link } from "react-router-dom";

import { Card, KeyValue, StateBadge } from "../../../components/ui";
import { Empty, QueryState } from "../../../kit/states";
import { elementHref } from "../../element/data/types";
import { useEntity, useTopologyParams } from "../data/queries";

/** The summary card. */
export function ElementSummary() {
  const { me } = useTopologyParams();
  const entity = useEntity(me);
  if (!me) return <Card section="topology.element" title="Element"><Empty title="No element in focus." /></Card>;
  const e = entity.data;
  const guards = Object.entries(e?.cellGuards ?? {});
  const distinct = (k: "sectorGroup" | "incidentZone") => [...new Set(guards.map(([, g]) => g[k]).filter(Boolean))].join(", ") || null;
  return (
    <Card section="topology.element" title={me} actions={<Link className="small" to={elementHref(me)}>Element detail →</Link>}>
      <QueryState q={entity} isEmpty={() => false}>
        {e && <>
          <KeyValue items={[
            ["Vendor", e.vendorName ? `${e.vendorName}${e.o1Protocol ? ` · O1 ${e.o1Protocol}` : ""}` : null],
            ["Type", e.entityType],
            ["Region / tenant", e.region || e.tenant ? `${e.region ?? "—"} / ${e.tenant ?? "—"}` : null],
            ["Cells", guards.length ? guards.map(([id]) => id).join(", ") : null],
            ["Sector group", distinct("sectorGroup")],
            ["Incident zone", distinct("incidentZone")],
            ["Guards", guards.filter(([, g]) => g.cellClass !== "NORMAL").length
              ? <span className="row wrap">{guards.filter(([, g]) => g.cellClass !== "NORMAL").map(([id, g]) => <span key={id} className="small">{id} <StateBadge state={g.cellClass} /></span>)}</span>
              : "all NORMAL"],
          ]} />
          <div className="row wrap">
            <Link className="btn small" to={`/alarms?me=${encodeURIComponent(me)}`}>Alarms</Link>
            <Link className="btn small" to={elementHref(me, "guards")}>Cell guards</Link>
          </div>
        </>}
      </QueryState>
    </Card>
  );
}
