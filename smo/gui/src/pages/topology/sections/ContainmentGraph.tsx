/** RAN topology · containment (`topology.containment`, GUI-3.2 to 3.4): the managed-object tree of RAN NF OAM (`GET /topology/graph`) drawn
 * as an indented tree, each node coloured by the worst open alarm on it, or, while it is folded, anywhere below it (the alarm overlay). Element
 * roots start folded, unless one element is focused (`?me=`), whose tree starts open. A node with children folds and unfolds with its arrow; a
 * node's name opens its element's page on the Managed objects tab (the drill-down). The top bar's scope narrows the network view. */
import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";

import { Card } from "../../../components/ui";
import { Empty, ErrorRetry, Skeleton } from "../../../kit/states";
import { elementHref } from "../../element/data/types";
import { buildTree, describeAlarms, severityFill, visibleRows, type Row } from "../data/containment";
import { GRAPH_NODES, useContainment, useTopologyParams } from "../data/queries";

/** Pixels per row and per level of the drawing. */
const ROW = 24, INDENT = 22;

/** The box. */
export function ContainmentGraph() {
  const { me } = useTopologyParams();
  const graph = useContainment(me);
  const [toggled, setToggled] = useState<Map<string, boolean>>(new Map());
  const tree = useMemo(() => (graph.data ? buildTree(graph.data) : null), [graph.data]);
  // An element in focus starts open to its leaves; in the network view only the roots show, coloured by the worst alarm below them.
  const isOpen = (dn: string) => toggled.get(dn) ?? me !== null;
  const view = tree ? visibleRows(tree, isOpen) : null;
  const toggle = (dn: string) => setToggled(new Map(toggled).set(dn, !isOpen(dn)));
  const alarmed = graph.data?.nodes.filter((n) => n.worst).length ?? 0;
  return (
    <Card section="topology.containment" title={me ? `Containment of ${me}` : "Containment tree"}
      sub={graph.data ? `${graph.data.total.toLocaleString("en-US")} managed objects · ${alarmed} with open alarms` : "managed objects and their open alarms"}>
      {graph.error && !graph.data ? <ErrorRetry error={graph.error} onRetry={() => void graph.refetch()} />
        : !view ? <Skeleton lines={6} />
          : view.rows.length === 0 ? <Empty title="No managed object in the tree.">An element is added when it is registered; its server's objects when it is walked (Element → Managed objects → Refresh).</Empty>
            : <TreeSvg rows={view.rows} onToggle={toggle} />}
      {graph.data?.truncated && <p className="gap-note">Showing the first {GRAPH_NODES} of {graph.data.total.toLocaleString("en-US")} objects (in DN order): focus one element, or narrow the scope, to see the rest.</p>}
      {view && view.hidden > 0 && <p className="gap-note">{view.hidden} more unfolded row(s) not drawn: fold a node to make room.</p>}
      <div className="legend">
        <span><i className="f-cr" />critical</span><span><i className="f-mj" />major</span><span><i className="f-mn" />minor</span>
        <span><i className="f-wn" />warning</span><span><i className="f-ok" />no open alarm</span><span>a folded node shows the worst below it</span>
      </div>
    </Card>
  );
}

/** The drawing: one row per visible node, an elbow from its parent, the fold arrow, the coloured dot and the name (a link to the element page). */
function TreeSvg({ rows, onToggle }: { rows: Row[]; onToggle: (dn: string) => void }) {
  const navigate = useNavigate();
  const width = 40 + Math.max(...rows.map((r) => r.depth)) * INDENT + 360;
  const parentRow = new Map<string, number>();
  return (
    <div className="graph">
      <svg viewBox={`0 0 ${width} ${rows.length * ROW + 8}`} role="tree" aria-label="Containment tree">
        {rows.map((r, i) => {
          parentRow.set(r.node.dn, i);
          const x = 16 + r.depth * INDENT, y = 14 + i * ROW;
          const from = r.node.parentDn !== null ? parentRow.get(r.node.parentDn) : undefined;
          const alarms = describeAlarms(r.node.alarms);
          const label = `${r.node.class}=${r.node.id}`;
          const open = () => navigate(elementHref(r.node.managedElementRef, "mo"));
          return (
            <g key={r.node.dn} role="treeitem" aria-level={r.depth + 1} aria-expanded={r.hasChildren ? r.open : undefined} data-dn={r.node.dn}
              data-colour={r.colour ?? "none"}>
              {from !== undefined && <path className="g-edge" fill="none" d={`M${x - INDENT + 4},${14 + from * ROW + 6} V${y} H${x - 7}`} />}
              {r.hasChildren && (
                <text className="g-fold" x={x - 15} y={y + 4} role="button" tabIndex={0} aria-label={`${r.open ? "Fold" : "Unfold"} ${label}`}
                  onClick={() => onToggle(r.node.dn)} onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onToggle(r.node.dn); } }}>
                  {r.open ? "▾" : "▸"}
                </text>
              )}
              <circle cx={x} cy={y} r={5} className={severityFill(r.colour)}><title>{alarms || "no open alarm"}</title></circle>
              <text x={x + 10} y={y + 4} className="g-label" role="link" tabIndex={0} aria-label={`${label}, ${alarms || "no open alarm"}; open ${r.node.managedElementRef}`}
                onClick={open} onKeyDown={(e) => { if (e.key === "Enter") open(); }}>
                {label}{alarms ? <tspan className="muted"> · {alarms}</tspan> : null}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
