/** Element detail · overview (`element.overview`): four tiles (critical alarms of all alarms, cells and their guards, config changes in its
 * history, neighbour relations and the one-way ones) and the element's O1 endpoint card (adaptor, services, conformance, pinned host keys,
 * onboarding). Counts come from one-row pages' `total`; the cells and relations of one element are bounded lists. Cell state, PCI and load
 * from the mockup's cell table are not served by any route (README, Known limits). */
import { Link } from "react-router-dom";

import { Card, Id, KeyValue, StateBadge } from "../../../components/ui";
import { formatCount, Kpi } from "../../../kit/Kpi";
import { countLinks } from "../../topology/data/graph";
import { useAlarmCount, useElementLinks, useEntity, useHistory, useHostKeys, useOnboarding } from "../data/queries";

/** The overview of element `me`. */
export function Overview({ me }: { me: string }) {
  const entity = useEntity(me);
  const critical = useAlarmCount(me, "critical");
  const all = useAlarmCount(me);
  const history = useHistory(me, { limit: 1, offset: 0 });
  const links = useElementLinks(me);
  const e = entity.data;
  const guards = Object.values(e?.cellGuards ?? {});
  const c = links.data ? countLinks(links.data) : null;
  const special = guards.filter((g) => g.cellClass !== "NORMAL").length;
  return (
    <div className="stack" data-section="element.overview">
      <div className="grid g4">
        <Kpi label="Critical alarms" value={critical.data?.total === undefined ? null : formatCount(critical.data.total)} tone={critical.data?.total ? "hot" : undefined}
          foot={all.data?.total !== undefined ? `${formatCount(all.data.total)} alarms in all, cleared included` : undefined} to={`/alarms?me=${encodeURIComponent(me)}`} />
        <Kpi label="Cells" value={e ? formatCount(guards.length) : null} foot={e ? (special ? `${special} guarded above NORMAL` : "all NORMAL") : undefined} />
        <Kpi label="Config changes" value={history.data?.total === undefined ? null : formatCount(history.data.total)} foot="snapshots in its history" />
        <Kpi label="Neighbour relations" value={c ? formatCount(c.total) : null}
          foot={c ? (c.notReciprocal ? <span className="t-bad">{c.notReciprocal} not reciprocal</span> : "all reciprocal") : undefined}
          to={`/topology?me=${encodeURIComponent(me)}`} />
      </div>
      <Endpoint me={me} endpointId={e?.o1AdaptorEndpointId ?? null} services={e?.supportedServices ?? null} conformance={e?.conformanceMode ?? null} />
    </div>
  );
}

/** The O1 endpoint card. */
function Endpoint({ me, endpointId, services, conformance }: { me: string; endpointId: string | null; services: string[] | null; conformance: string | null }) {
  const keys = useHostKeys(endpointId);
  const onboarding = useOnboarding(me);
  const key = keys.data?.[0];
  return (
    <Card section="element.endpoint" title="Endpoint" actions={<Link className="small" to="/configuration#trust">Trust &amp; keys →</Link>}>
      <KeyValue items={[
        ["O1 adaptor", endpointId ? <Id value={endpointId} /> : "none registered"],
        ["MnS services", services?.length ? services.join(", ") : null],
        ["Conformance", conformance],
        ["Host key", key ? `${key.keyType} · pinned by ${key.pinnedBy}` : keys.error ? <span className="muted">not an ssh endpoint, or not readable</span> : keys.data ? <span className="t-bad">none pinned</span> : null],
        ["Onboarding", onboarding.data ? <StateBadge state={onboarding.data.status} /> : onboarding.error?.status === 404 ? <span className="muted">not onboarded through a template</span> : null],
      ]} />
    </Card>
  );
}
