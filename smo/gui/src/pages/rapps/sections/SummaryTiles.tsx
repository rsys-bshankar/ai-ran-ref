/** The rApps page's tiles (SCALE.md P2): true counts of instances by state and of packages, from one summary call (`/api/summary/rapps`).
 * The autonomy mix, actions and refusals per rApp in 24 h are not served by the backend, so their tiles read "—" with a gap note.
 * Section id `rapps.tiles`. */
import { Kpi, formatCount } from "../../../kit/Kpi";
import { ErrorRetry } from "../../../kit/states";
import { count } from "../../../data/summary";
import { useRappsSummary } from "../data/queries";

/** The tile row. */
export function SummaryTiles() {
  const s = useRappsSummary();
  const c = (k: string) => count(s.data, k);
  if (s.error && !s.data) return <ErrorRetry error={s.error} onRetry={() => void s.refetch()} />;
  const faulted = c("instances.FAULTED");
  return (
    <div data-section="rapps.tiles" className="stack">
      <div className="grid g5">
        <Kpi label="Running" value={formatCount(c("instances.RUNNING"))} foot={`of ${formatCount(c("instances.total"))} instances`} to="/rapps#instances" />
        <Kpi label="Upgrading" value={formatCount(c("instances.UPGRADING"))} foot="see Rollouts" to="/rapps#rollouts" />
        <Kpi label="Faulted" value={formatCount(faulted)} foot="recover from the table" tone={faulted ? "hot" : undefined} />
        <Kpi label="Deploying" value={formatCount(c("instances.DEPLOYING"))} foot="waiting for bootstrap-complete" />
        <Kpi label="Packages" value={formatCount(c("packages.total"))} foot={`${formatCount(c("packages.AVAILABLE"))} available · ${formatCount(c("packages.FAILED"))} failed`} to="/rapps#packages" />
      </div>
      {s.data && s.data.partial.length > 0 && <p className="small muted">Partial: {s.data.partial.join(", ")} did not answer.</p>}
      <p className="gap-note">Autonomy mix, actions in 24 h and refusals per rApp are not served by the backend yet.</p>
    </div>
  );
}
