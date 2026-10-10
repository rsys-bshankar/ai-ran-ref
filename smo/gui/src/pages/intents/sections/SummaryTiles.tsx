/** Section `intents.tiles`: the Intents tab's four tiles. Activated and deactivated are true counts from the BFF summary "intents"
 * (`intents.ACTIVATED`, `intents.DEACTIVATED`, `intents.total`); "not fulfilled" and "in conflict" are ⚠ gaps (no count of them is served, and
 * counting them would read every intent's reports), so they show "—" with a note. */
import { count } from "../../../data/summary";
import { Kpi, formatCount } from "../../../kit/Kpi";
import { ErrorRetry } from "../../../kit/states";
import { useIntentSummary } from "../data/queries";

/** The tiles. */
export function SummaryTiles() {
  const summary = useIntentSummary();
  const s = summary.data;
  const total = count(s, "intents.total");
  return (
    <section className="col" data-section="intents.tiles">
      {summary.error && !s && <ErrorRetry error={summary.error} onRetry={() => void summary.refetch()} />}
      <div className="grid g4">
        <Kpi label="Activated" value={s ? formatCount(count(s, "intents.ACTIVATED")) : null} unit={total !== null ? `/ ${formatCount(total)}` : undefined} foot="Intent Service · admin state" />
        <Kpi label="Deactivated" value={s ? formatCount(count(s, "intents.DEACTIVATED")) : null} foot="paused by their creator" />
        <Kpi label="Not fulfilled" value={null} foot={<span className="gap-note">no count served</span>} />
        <Kpi label="In conflict" value={null} foot={<span className="gap-note">no count served</span>} />
      </div>
      {s && s.partial.length > 0 && <span className="small muted">Partial: {s.partial.join(", ")} did not answer.</span>}
    </section>
  );
}
