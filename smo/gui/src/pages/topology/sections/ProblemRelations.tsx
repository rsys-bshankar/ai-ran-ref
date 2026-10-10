/** RAN topology · relations that need attention (`topology.problems`): the declared neighbour relations that are not reciprocal, point
 * outside what is managed here, or name a cell id several elements claim, everywhere or only around the focused element. Paged and filtered by
 * RAN NF OAM (`/topology/links?reciprocal=false&link_type=…&limit&offset`): a one-way relation is between elements (`INTER_ELEMENT`, the
 * default) or within one (`INTRA_ELEMENT`), picked with "Between". Each row links to the cell guards on the Element page where the fix is made. */
import { useState } from "react";
import { Link } from "react-router-dom";

import type { Query } from "../../../api/client";
import { Card } from "../../../components/ui";
import { Badge } from "../../../kit/Badge";
import { Segmented } from "../../../kit/Segmented";
import { ServerTable } from "../../../kit/ServerTable";
import { elementHref, type CellLink } from "../../element/data/types";
import { fixHint } from "../data/graph";
import { LINKS_PATH, useTopologyParams, type ProblemKind } from "../data/queries";

/** The tone of a link type badge. */
const TYPE_TONE = { INTER_ELEMENT: "info", INTRA_ELEMENT: "ok", EXTERNAL: "warn", AMBIGUOUS: "bad" } as const;

/** The route filters of one problem kind (`oneWay` picks which one-way relations: between elements or within one). */
export function problemQuery(kind: ProblemKind, oneWay: "inter" | "intra", me: string | null): Query {
  const where = me ? { managed_element_ref: me } : {};
  if (kind === "external") return { ...where, link_type: "EXTERNAL" };
  if (kind === "ambiguous") return { ...where, link_type: "AMBIGUOUS" };
  return { ...where, reciprocal: false, link_type: oneWay === "inter" ? "INTER_ELEMENT" : "INTRA_ELEMENT" };
}

/** The table card. */
export function ProblemRelations() {
  const { me, problem, setProblem } = useTopologyParams();
  const [scope, setScope] = useState<"all" | "focus">("all");
  const [oneWay, setOneWay] = useState<"inter" | "intra">("inter");
  const focused = scope === "focus" && me !== null;
  const yesNo = (v: boolean, l: CellLink) => (l.linkType === "EXTERNAL" || l.linkType === "AMBIGUOUS" ? <span className="muted">—</span> : v ? "yes" : "no");
  return (
    <Card section="topology.problems" title="Relations that need attention" sub="/topology/links · not reciprocal, external or ambiguous · paged on the server"
      actions={<>
        <Segmented<ProblemKind> label="Problem" value={problem} onChange={setProblem}
          options={[{ id: "oneway", label: "Not reciprocal" }, { id: "external", label: "External" }, { id: "ambiguous", label: "Ambiguous" }]} />
        {problem === "oneway" && <Segmented label="Between" value={oneWay} onChange={setOneWay}
          options={[{ id: "inter", label: "Elements" }, { id: "intra", label: "Cells of one element" }]} />}
        <Segmented label="Where" value={scope} onChange={setScope}
          options={[{ id: "all", label: "Everywhere" }, { id: "focus", label: me ? `Around ${me}` : "Around the focused element", title: me ? undefined : "Focus an element first" }]} />
      </>}>
      <ServerTable<CellLink> path={LINKS_PATH} query={problemQuery(problem, oneWay, focused ? me : null)} refetchInterval={60_000}
        rowKey={(l) => `${l.aElement}/${l.aCell}>${l.bElement ?? "?"}/${l.bCell}`}
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
    </Card>
  );
}
