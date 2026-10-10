/** Section `kpis.tiles` (Overview): five KPI tiles, each a standard KPI computed by RAN NF OAM over the chosen range for everything in scope
 * (`GET /ran-nf-oam/kpis/{name}?group_by=all`, a ratio from summed counters, never an average of cell ratios). A KPI that is not defined (404),
 * or has no data in the window, shows "—" and says why. The tiles carry no sparkline: the backend computes one window per call and serves no
 * bucketed series (⚠ gap, README). */
import { Kpi } from "../../../kit/Kpi";
import { useKpi, type Range } from "../data/queries";

/** The five tiles: the standard KPI set's names (ran-nf-oam/app/kpi.py STANDARD_KPIS). */
export const TILE_KPIS = [
  { name: "dl_ue_throughput", label: "DL UE throughput" },
  { name: "dl_prb_utilization", label: "DL PRB utilisation" },
  { name: "handover_success_rate", label: "Handover success" },
  { name: "handover_failure_rate", label: "Handover failure" },
  { name: "rrc_connected_ues_mean", label: "RRC-connected UEs" },
] as const;

/** Units as the tile prints them. */
const UNIT: Record<string, string> = { percent: "%", "Mbit/s": "Mbit/s", ues: "UEs" };

/** The row of tiles. */
export function KpiTiles({ range }: { range: Range }) {
  return (
    <section className="grid g5" data-section="kpis.tiles" aria-label="Network KPIs">
      {TILE_KPIS.map((k) => <KpiTile key={k.name} name={k.name} label={k.label} range={range} />)}
    </section>
  );
}

/** One tile. */
function KpiTile({ name, label, range }: { name: string; label: string; range: Range }) {
  const q = useKpi(name, range);
  const item = q.data?.items[0];
  const v = item?.value;
  const status = q.error ? ((q.error as { status?: number }).status === 404 ? "not defined (Definitions tab)" : "unavailable")
    : !q.data ? "computing…" : v === null || v === undefined ? (item?.reason === "UNDEFINED" ? "undefined (no attempts)" : "no data in window") : `${item!.samples.toLocaleString("en-US")} observations · ${range}`;
  return (
    <Kpi label={label} value={typeof v === "number" ? Number(v.toPrecision(4)).toLocaleString("en-US") : null}
      unit={q.data?.unit ? UNIT[q.data.unit] ?? q.data.unit : undefined} foot={status} title={`RAN NF OAM KPI ${name}`} />
  );
}
