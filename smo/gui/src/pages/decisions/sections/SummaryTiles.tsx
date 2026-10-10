/** Section `decisions.tiles`: four tiles of the last 24 h, counted on the server (BFF summary `decisions24h.*`), never from the list page:
 * all decisions, written at once (DIRECT), approved by a person, and not written (rejected, lapsed or refused). */
import { Card } from "../../../components/ui";
import { count, sum } from "../../../data/summary";
import { formatCount, Kpi } from "../../../kit/Kpi";
import { ErrorRetry } from "../../../kit/states";
import { useDecisionSummary } from "../data/queries";

/** The tiles. */
export function SummaryTiles() {
  const summary = useDecisionSummary();
  const s = summary.data;
  const v = (n: number | null) => (s ? formatCount(n) : "…");
  return (
    <Card section="decisions.tiles">
      {summary.error && !s && <ErrorRetry error={summary.error} onRetry={() => void summary.refetch()} />}
      <div className="grid g4">
        <Kpi label="Decisions · 24 h" value={v(count(s, "decisions24h.total"))} foot="counted on the server" tone="volt" />
        <Kpi label="Autonomous" value={v(count(s, "decisions24h.DIRECT"))} foot="written at once, within safeguards" />
        <Kpi label="Approved by a person" value={v(count(s, "decisions24h.APPROVED"))} foot="held, then approved" />
        <Kpi label="Not written" value={v(sum(s, ["decisions24h.REJECTED", "decisions24h.EXPIRED", "decisions24h.REFUSED"]))} foot="rejected, lapsed or refused" />
      </div>
      {s && s.partial.length > 0 && <p className="small muted">Partial: {s.partial.join(", ")} did not answer.</p>}
    </Card>
  );
}
