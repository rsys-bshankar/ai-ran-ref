/** RAN topology · relations that need attention (`topology.problems`): the declared neighbour relations that are not reciprocal, point
 * outside what is managed here, or name a cell id several elements claim, everywhere or only around the focused element. Each row links to
 * the cell guards on the Element page where the fix is made. `/topology/links` is not paged and has no `reciprocal` filter, so this box
 * filters and pages the route's answer in the browser (50 rows a page); the README lists this as a known limit. */
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

import { Card, DataTable } from "../../../components/ui";
import { Badge } from "../../../kit/Badge";
import { Pager } from "../../../kit/Pager";
import { Segmented } from "../../../kit/Segmented";
import { ErrorRetry } from "../../../kit/states";
import { elementHref, type CellLink } from "../../element/data/types";
import { fixHint, problemLinks } from "../data/graph";
import { useLinks, useTopologyParams, type ProblemKind } from "../data/queries";

const PAGE = 50;

/** The tone of a link type badge. */
const TYPE_TONE = { INTER_ELEMENT: "info", INTRA_ELEMENT: "ok", EXTERNAL: "warn", AMBIGUOUS: "bad" } as const;

/** The table card. */
export function ProblemRelations() {
  const { me, problem, setProblem } = useTopologyParams();
  const [scope, setScope] = useState<"all" | "focus">("all");
  const focused = scope === "focus" && me !== null;
  const links = useLinks(focused ? me : null);
  const [offset, setOffset] = useState(0);
  const rows = useMemo(() => (links.data ? problemLinks(links.data, problem) : undefined), [links.data, problem]);
  useEffect(() => { setOffset(0); }, [problem, scope, me]);
  const yesNo = (v: boolean, l: CellLink) => (l.linkType === "EXTERNAL" || l.linkType === "AMBIGUOUS" ? <span className="muted">—</span> : v ? "yes" : "no");
  return (
    <Card section="topology.problems" title="Relations that need attention" sub="/topology/links · not reciprocal, external or ambiguous"
      actions={<>
        <Segmented<ProblemKind> label="Problem" value={problem} onChange={setProblem}
          options={[{ id: "oneway", label: "Not reciprocal" }, { id: "external", label: "External" }, { id: "ambiguous", label: "Ambiguous" }]} />
        <Segmented label="Where" value={scope} onChange={setScope}
          options={[{ id: "all", label: "Everywhere" }, { id: "focus", label: me ? `Around ${me}` : "Around the focused element", title: me ? undefined : "Focus an element first" }]} />
      </>}>
      {links.error && !links.data ? <ErrorRetry error={links.error} onRetry={() => void links.refetch()} /> : (
        <DataTable rows={rows?.slice(offset, offset + PAGE)} loading={links.isLoading} rowKey={(l) => `${l.aElement}/${l.aCell}>${l.bElement ?? "?"}/${l.bCell}`}
          empty={problem === "oneway" ? "Every relation is declared on both sides." : problem === "external" ? "Every neighbour is managed here." : "No cell id is claimed by two elements."}
          columns={[
            { header: "A cell", render: (l) => <Link to={elementHref(l.aElement, "guards")}>{l.aElement} / {l.aCell}</Link> },
            { header: "B cell", render: (l) => <span className="mono small">{l.bElement ? `${l.bElement} / ${l.bCell}` : l.bCell}</span> },
            { header: "Link type", render: (l) => <Badge tone={TYPE_TONE[l.linkType]}>{l.linkType}</Badge> },
            { header: "Reciprocal", render: (l) => (l.linkType === "EXTERNAL" || l.linkType === "AMBIGUOUS" ? <Badge tone="mute">n/a</Badge> : <Badge tone={l.reciprocal ? "ok" : "bad"}>{l.reciprocal ? "yes" : "no"}</Badge>) },
            { header: "Same sector group", render: (l) => yesNo(l.sameSectorGroup, l) },
            { header: "Same incident zone", render: (l) => yesNo(l.sameIncidentZone, l) },
            { header: "Fix", render: (l) => { const f = fixHint(l); return f.element ? <Link to={elementHref(f.element, "guards")}>{f.text}</Link> : <span className="muted">{f.text}</span>; } },
          ]} />
      )}
      {rows && <Pager offset={offset} limit={PAGE} shown={rows.slice(offset, offset + PAGE).length} total={rows.length} onOffset={setOffset} />}
    </Card>
  );
}
