/** Section `kpis.worst` (Overview): the ten managed elements with the lowest DL UE throughput over the range, ranked from RAN NF OAM's KPI
 * computation per element (`GET /ran-nf-oam/kpis/dl_ue_throughput?group_by=element`; the server computes up to 1,000 groups and the box shows
 * only ten, SCALE.md P4). The per-DU throughput band chart of the mockup (p10–p90 over time) is a ⚠ gap: no bucketed series is served. */
import { Card } from "../../../components/ui";
import { Empty, ErrorRetry, Skeleton } from "../../../kit/states";
import { useKpi, type Range } from "../data/queries";

/** How many elements the box ranks. */
const TOP = 10;

/** The box. */
export function WorstElements({ range }: { range: Range }) {
  const q = useKpi("dl_ue_throughput", range, "element");
  const ranked = (q.data?.items ?? []).filter((i) => typeof i.value === "number").sort((a, b) => a.value! - b.value!).slice(0, TOP);
  const max = Math.max(1, ...ranked.map((i) => i.value!));
  const notDefined = (q.error as { status?: number } | null)?.status === 404;
  return (
    <Card section="kpis.worst" title="Lowest DL throughput · 10 elements" sub={`dl_ue_throughput per managed element · ${range}${q.data?.unit ? ` · ${q.data.unit}` : ""}`}>
      {q.error && !q.data ? (notDefined ? <Empty title="The KPI dl_ue_throughput is not defined.">Add the standard set in the Definitions tab.</Empty> : <ErrorRetry error={q.error} onRetry={() => void q.refetch()} />)
        : !q.data ? <Skeleton lines={5} />
          : ranked.length === 0 ? <Empty title="No throughput data in this window." />
            : <ol className="list" aria-label="Elements with the lowest throughput">
              {ranked.map((i) => (
                <li key={JSON.stringify(i.group)}>
                  <span className="mono small grow">{Object.values(i.group).join(" / ") || "—"}</span>
                  <span className="meter" style={{ width: 90 }}><span className="f-bad" style={{ width: `${Math.round((100 * i.value!) / max)}%` }} /></span>
                  <span className="mono xs">{Number(i.value!.toPrecision(3))}</span>
                </li>
              ))}
            </ol>}
      {q.data?.truncated && <p className="gap-note">The server capped the computation (too many files or groups); the ranking covers what it read.</p>}
      <p className="gap-note">The throughput distribution over time (p10–p90 band) needs a bucketed series the backend does not serve.</p>
    </Card>
  );
}
