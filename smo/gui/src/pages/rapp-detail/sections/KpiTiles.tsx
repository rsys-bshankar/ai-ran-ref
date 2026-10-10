/** The rApp detail KPI tiles: decisions in the last 24 h (a one-row page's `total`, `/ran-nf-oam/decision-records?invoker_id=&since=`), config
 * jobs this hour against the rApp's limit, performance reports and faults reported (bounded reads shared with the boxes below), and the rApp's headline
 * KPI: the first metric of its newest performance report (`/rapp-mgmt/instances/{id}/performance/latest`), the others in the foot. Section id `rapp.kpis`. */
import { Kpi, formatCount } from "../../../kit/Kpi";
import { formatTime } from "../../../lib/domain";
import { headlineOf, useLatestKpi } from "../../rapps/data/queries";
import { formatMetric } from "../../rapps/sections/HeadlineKpi";
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
  const latest = useLatestKpi(id);
  const head = headlineOf(latest.data);
  const rest = Object.entries(latest.data?.metrics ?? {}).slice(1, 4).map(([k, v]) => `${k} ${formatMetric(v)}`).join(" · ");
  return (
    <div className="grid g5" data-section="rapp.kpis">
      <Kpi label={head ? `Headline KPI · ${head.name}` : "Headline KPI"} value={head ? formatMetric(head.value) : latest.data ? "—" : null}
        foot={head ? `${rest ? `${rest} · ` : ""}reported ${formatTime(latest.data?.at)}` : latest.data ? "no performance report yet" : undefined} />
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
