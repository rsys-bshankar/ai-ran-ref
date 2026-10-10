/** An rApp's headline KPI as a compact cell: the first metric of its newest performance report ("prbUsage 41.2"), every other metric and the
 * report time in the tooltip, "—" when it reported nothing. Used by the instance table and the attention cards of the rApps page. */
import { formatTime } from "../../../lib/domain";
import { headlineOf, type LatestKpi } from "../data/queries";

/** A number with at most three significant digits past the point (large numbers whole). */
export function formatMetric(v: number): string {
  return Math.abs(v) >= 100 ? v.toFixed(0) : Number(v.toPrecision(3)).toString();
}

/** The cell. */
export function HeadlineKpi({ kpi }: { kpi: LatestKpi | undefined }) {
  const h = headlineOf(kpi);
  if (!h || !kpi) return <span className="muted" title="No performance report yet">—</span>;
  const all = Object.entries(kpi.metrics).map(([k, v]) => `${k} ${formatMetric(v)}`).join(" · ");
  return (
    <span className="small" title={`${all} · reported ${formatTime(kpi.at)}`}>
      <span className="muted">{h.name}</span> <strong>{formatMetric(h.value)}</strong>{h.others > 0 && <span className="xs muted"> +{h.others}</span>}
    </span>
  );
}
