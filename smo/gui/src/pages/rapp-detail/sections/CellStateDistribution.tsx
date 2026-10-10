/** The rApp detail "Cells by state · 24 h" box of the design: a ⚠ data gap (BRIEF §5: per-rApp cell-state history). No backend serves the
 * cells in an rApp's scope or their state over time, so the box shows only that note instead of a chart. Section id `rapp.cells`. */
import { Card } from "../../../components/ui";

/** The placeholder box. */
export function CellStateDistribution() {
  return (
    <Card section="rapp.cells" title="Cells by state · 24 h">
      <p className="gap-note">Not available: the backend does not serve the cells in an rApp's scope or their state history. A rApp that reports them shows them on its own operator page below.</p>
    </Card>
  );
}
