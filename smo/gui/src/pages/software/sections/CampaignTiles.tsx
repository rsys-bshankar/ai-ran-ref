/** Software · campaign tiles (`software.tiles`): campaigns by state from the BFF summary (`useSummary("software")`, true counts, SCALE.md P2).
 * The running/halted tile filters the campaign list to the halted ones. The mockup's version share, gate failures over 7 days and failed
 * element jobs over 24 h have no route that serves them: they are left out (README, Known limits). */
import { formatCount, Kpi } from "../../../kit/Kpi";
import { Meter } from "../../../kit/Meter";
import { ErrorRetry } from "../../../kit/states";
import { useUrlParam } from "../../element/data/url";
import { count, sum, useSummary } from "../../../data/summary";

/** The tile row. */
export function CampaignTiles() {
  const s = useSummary("software");
  const [status, setStatus] = useUrlParam("status");
  if (s.error && !s.data) return <section data-section="software.tiles"><ErrorRetry error={s.error} onRetry={() => void s.refetch()} /></section>;
  const c = (k: string) => count(s.data, `campaigns.${k}`);
  const running = c("RUNNING"), halted = c("HALTED"), total = c("total");
  const back = sum(s.data, ["campaigns.ROLLING_BACK", "campaigns.ROLLED_BACK"]);
  const parts = [
    { key: "running", value: running ?? 0, tone: "volt" }, { key: "halted", value: halted ?? 0, tone: "bad" },
    { key: "completed", value: c("COMPLETED") ?? 0, tone: "ok" }, { key: "back", value: back ?? 0, tone: "warn" },
  ];
  return (
    <div className="grid g4" data-section="software.tiles">
      <Kpi label="Running · halted" value={running === null || halted === null ? null : `${formatCount(running)} · ${formatCount(halted)}`}
        foot={halted ? `${formatCount(halted)} waiting for you` : "none waiting"} tone={halted ? "hot" : undefined}
        onClick={() => setStatus(status === "HALTED" ? null : "HALTED")} active={status === "HALTED"} title="Show the halted campaigns" />
      <Kpi label="Completed" value={c("COMPLETED") === null ? null : formatCount(c("COMPLETED"))} foot={c("ABORTED") !== null ? `${formatCount(c("ABORTED"))} aborted` : undefined} />
      <Kpi label="Rolled back" value={back === null ? null : formatCount(back)} foot={c("ROLLBACK_FAILED") ? <span className="t-bad">{formatCount(c("ROLLBACK_FAILED"))} rollback failed</span> : "no failed rollback"}
        tone={c("ROLLBACK_FAILED") ? "warm" : undefined} />
      <Kpi label="All campaigns" value={total === null ? null : formatCount(total)} foot={s.data?.partial.length ? "partial: RAN NF OAM did not answer" : undefined}>
        {total ? <Meter parts={parts} total={total} label="Campaigns by state" /> : null}
      </Kpi>
    </div>
  );
}
