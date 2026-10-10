/** The rApp detail KPI tiles: decisions in the last 24 h (a one-row page's `total`, `/ran-nf-oam/decision-records?invoker_id=&since=`), config
 * jobs this hour against the rApp's limit, performance reports and faults reported (bounded reads shared with the boxes below). The rApp's own
 * headline KPI is not served by the backend (BRIEF §5), so its tile reads "—". Section id `rapp.kpis`. */
import { Kpi, formatCount } from "../../../kit/Kpi";
import { useDecisionCount24h, usePerformance, useRecentFaults, useSafeguards } from "../data/queries";

/** The tile row of instance `id`. */
export function KpiTiles({ id }: { id: string }) {
  const sg = useSafeguards(id);
  const invoker = sg.data?.invokerId ?? null;
  const decisions = useDecisionCount24h(invoker);
  const perf = usePerformance(id);
  const faults = useRecentFaults(id);
  const limits = sg.data?.limits;
  const critical = (faults.data ?? []).filter((f) => f.severity === "critical").length;
  return (
    <div className="grid g5" data-section="rapp.kpis">
      <Kpi label="Headline KPI" value={null} foot={<span className="gap-note">not served per rApp yet</span>} />
      <Kpi label="Decisions · 24 h" value={invoker ? (decisions.data?.total !== undefined ? formatCount(decisions.data.total) : null) : null}
        foot={invoker ? "decision records of this rApp" : "no credential: nothing decided"} to={invoker ? `/decisions?invoker=${invoker}` : undefined} />
      <Kpi label="Config jobs this hour" value={limits ? formatCount(limits.configJobsLastHour) : null}
        foot={limits?.maxConfigJobsPerHour != null ? `limit ${limits.maxConfigJobsPerHour} per hour` : "no hourly limit set"} />
      <Kpi label="Performance reports" value={perf.data ? (perf.data.length >= 50 ? "50+" : String(perf.data.length)) : null} foot="newest 50 read" />
      <Kpi label="Faults" value={faults.data ? (faults.data.length >= 50 ? "50+" : String(faults.data.length)) : null}
        foot={critical ? `${critical} critical among the newest 50` : "newest 50 read"} tone={critical ? "hot" : undefined} />
    </div>
  );
}
