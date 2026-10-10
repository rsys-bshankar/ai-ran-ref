/** RAN topology · relation tiles (`topology.tiles`): guarded cells and elements, every declared neighbour relation, and the three problem
 * counts (not reciprocal between managed cells, external, ambiguous). A problem tile is also the filter of the "relations that need attention"
 * table below. Every number is counted by RAN NF OAM: `/topology/links/counts` (the list is never read for them) and a one-row page's `total`
 * for the cells and elements. */
import { formatCount, Kpi } from "../../../kit/Kpi";
import { ErrorRetry } from "../../../kit/states";
import { oneWayCount, useElementCount, useGuardedCellCount, useLinkCounts, useTopologyParams, type ProblemKind } from "../data/queries";

/** The tile row. */
export function RelationTiles() {
  const counts = useLinkCounts();
  const cells = useGuardedCellCount();
  const elements = useElementCount();
  const { problem, setProblem } = useTopologyParams();
  if (counts.error && !counts.data) return <section data-section="topology.tiles"><ErrorRetry error={counts.error} onRetry={() => void counts.refetch()} /></section>;
  const c = counts.data;
  const tile = (kind: ProblemKind, label: string, value: number | undefined, foot: string) => (
    <Kpi label={label} value={value === undefined ? null : formatCount(value)} foot={foot} tone={value ? "hot" : undefined}
      onClick={() => setProblem(kind)} active={problem === kind} title="Show these relations in the table below" />
  );
  return (
    <div className="grid g5" data-section="topology.tiles">
      <Kpi label="Cells with guards" value={cells.data?.total !== undefined ? formatCount(cells.data.total) : null}
        foot={elements.data?.total !== undefined ? `${formatCount(elements.data.total)} managed elements` : "managed elements —"} />
      <Kpi label="Neighbour relations" value={c ? formatCount(c.total) : null} foot={c ? `${formatCount(c.interElement)} inter-element` : undefined} />
      {tile("oneway", "Not reciprocal", c ? oneWayCount(c) : undefined, "A lists B, B doesn't list A")}
      {tile("external", "External", c?.external, "neighbour not managed here")}
      {tile("ambiguous", "Ambiguous", c?.ambiguous, "cell id on 2+ elements")}
    </div>
  );
}
