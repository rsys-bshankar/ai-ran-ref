/** Configuration · job tiles (`configuration.tiles`): config jobs by state from the BFF summary (`useSummary("configuration")`, true counts,
 * SCALE.md P2). The halted tile (jobs waiting for an operator) and the failed tile filter the job list. */
import { count, sum, useSummary } from "../../../data/summary";
import { formatCount, Kpi } from "../../../kit/Kpi";
import { ErrorRetry } from "../../../kit/states";
import { useJobStatus } from "../data/queries";

/** The tile row. */
export function ConfigTiles() {
  const s = useSummary("configuration");
  const [status, setStatus] = useJobStatus();
  if (s.error && !s.data) return <section data-section="configuration.tiles"><ErrorRetry error={s.error} onRetry={() => void s.refetch()} /></section>;
  const c = (k: string) => count(s.data, `configJobs.${k}`);
  const fmt = (n: number | null) => (n === null ? null : formatCount(n));
  const toggle = (k: string) => setStatus(status === k ? "all" : k);
  return (
    <div className="grid g5" data-section="configuration.tiles">
      <Kpi label="Halted" value={fmt(c("HALTED"))} foot="waiting for an operator" tone={c("HALTED") ? "hot" : undefined}
        onClick={() => toggle("HALTED")} active={status === "HALTED"} title="Show the halted jobs" />
      <Kpi label="Running" value={fmt(sum(s.data, ["configJobs.PENDING", "configJobs.PROCESSING"]))} foot={`${fmt(c("PENDING")) ?? "—"} pending`}
        onClick={() => toggle("PROCESSING")} active={status === "PROCESSING"} />
      <Kpi label="Completed" value={fmt(c("COMPLETED"))} onClick={() => toggle("COMPLETED")} active={status === "COMPLETED"} />
      <Kpi label="Partial / failed" value={fmt(sum(s.data, ["configJobs.PARTIAL_SUCCESS", "configJobs.FAILED"]))}
        foot={`${fmt(c("FAILED")) ?? "—"} failed`} tone={c("FAILED") ? "warm" : undefined} onClick={() => toggle("FAILED")} active={status === "FAILED"} />
      <Kpi label="All jobs" value={fmt(c("total"))} foot={s.data?.partial.length ? "partial: RAN NF OAM did not answer" : "every write to the RAN"}
        onClick={() => setStatus("all")} active={status === "all"} />
    </div>
  );
}
