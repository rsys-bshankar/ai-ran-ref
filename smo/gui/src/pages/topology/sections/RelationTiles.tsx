/** RAN topology · relation tiles (`topology.tiles`): guarded cells and elements, every declared neighbour relation, and the three problem
 * counts (not reciprocal, external, ambiguous). A problem tile is also the filter of the "relations that need attention" table below.
 * The counts are true totals: `/topology/links` answers the whole list (it is not paged), and the cell and element counts read a one-row
 * page's `total`. No summary route counts relations yet (README, Known limits). */
import { formatCount, Kpi } from "../../../kit/Kpi";
import { ErrorRetry } from "../../../kit/states";
import { countLinks } from "../data/graph";
import { useElementCount, useGuardedCellCount, useLinks, useTopologyParams, type ProblemKind } from "../data/queries";

/** The tile row. */
export function RelationTiles() {
  const links = useLinks();
  const cells = useGuardedCellCount();
  const elements = useElementCount();
  const { problem, setProblem } = useTopologyParams();
  if (links.error && !links.data) return <section data-section="topology.tiles"><ErrorRetry error={links.error} onRetry={() => void links.refetch()} /></section>;
  const c = links.data ? countLinks(links.data) : null;
  const tile = (kind: ProblemKind, label: string, value: number | undefined, foot: string) => (
    <Kpi label={label} value={value === undefined ? null : formatCount(value)} foot={foot} tone={value ? "hot" : undefined}
      onClick={() => setProblem(kind)} active={problem === kind} title="Show these relations in the table below" />
  );
  return (
    <div className="grid g5" data-section="topology.tiles">
      <Kpi label="Cells with guards" value={cells.data?.total !== undefined ? formatCount(cells.data.total) : null}
        foot={elements.data?.total !== undefined ? `${formatCount(elements.data.total)} managed elements` : "managed elements —"} />
      <Kpi label="Neighbour relations" value={c ? formatCount(c.total) : null} foot={c ? `${formatCount(c.inter)} inter-element` : undefined} />
      {tile("oneway", "Not reciprocal", c?.notReciprocal, "A lists B, B doesn't list A")}
      {tile("external", "External", c?.external, "neighbour not managed here")}
      {tile("ambiguous", "Ambiguous", c?.ambiguous, "cell id on 2+ elements")}
    </div>
  );
}
