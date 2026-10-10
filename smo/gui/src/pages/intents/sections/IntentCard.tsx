/** Section `intents.card`: one intent as a card (the selected intent under the table, or one card of the cards view). Admin state, priority, id
 * and label; a fulfilment `RingGauge` (share of targets the newest fulfilment report says are FULFILLED, "—" when it has none: the percent is a ⚠
 * gap); the expectation box; the handler and the time of the newest report; the role-gated actions and "Reports"; the conflict callout when the
 * handler reported conflicts; and the negotiation-feedback box. Reads the intent's newest CARD_REPORTS reports (one call). */
import type { Intent } from "../../../api/types";
import { RingGauge } from "../../../components/charts";
import { Id, StateBadge } from "../../../components/ui";
import { formatTime } from "../../../lib/domain";
import { Badge } from "../../../kit/Badge";
import { Callout } from "../../../kit/Callout";
import { ErrorRetry } from "../../../kit/states";
import { conflicts, expectations, fulfilment, negotiation } from "../data/intent";
import { useIntentReports } from "../data/queries";
import { IntentActions } from "./IntentActions";
import { NegotiationFeedback } from "./NegotiationFeedback";

/** The card. `onReports` opens the reports drawer. */
export function IntentCard({ intent, onReports }: { intent: Intent; onReports: (i: Intent) => void }) {
  const reports = useIntentReports(intent.intentId);
  const f = fulfilment(reports.data);
  const c = conflicts(reports.data);
  const exps = expectations(intent.attributes ?? {});
  return (
    <article className="card" data-section="intents.card" data-intent={intent.intentId}>
      <div className="row between top">
        <div className="col">
          <div className="row wrap"><StateBadge state={intent.intentAdminState} /><Badge plain>priority {intent.intentPriority}</Badge><Id value={intent.intentId} /></div>
          <h2>{intent.userLabel ?? <span className="muted">No label</span>}</h2>
        </div>
        <div className="col" style={{ alignItems: "center", gap: 2 }}>
          <RingGauge value={f?.pct ?? null} size={72} label="Targets fulfilled" />
          <span className="xs muted">{f ? (f.targetsTotal ? `${f.targetsMet}/${f.targetsTotal} targets met` : f.status) : "no fulfilment report"}</span>
        </div>
      </div>
      {reports.error && !reports.data && <ErrorRetry error={reports.error} onRetry={() => void reports.refetch()} />}
      <div className="inset col">
        <span className="xs muted">Expectation</span>
        {exps.length === 0 ? <span className="muted small">—</span> : exps.map((e, i) => (
          <div key={i} className="col" style={{ gap: 2 }}><span className="mono small">{e.objectType}</span><span className="xs muted">{e.targets.join(" · ") || "no targets"}</span></div>
        ))}
      </div>
      <div className="row between wrap">
        <span className="small muted">Handler <strong>{intent.rmihId}</strong>{f ? ` · reported ${formatTime(f.at)}` : ""}</span>
        <div className="row wrap">
          {f && <StateBadge state={f.status === "FULFILLED" ? "FULFILLED" : f.state ?? f.status} />}
          <button type="button" className="btn small" onClick={() => onReports(intent)}>Reports</button>
          <IntentActions intent={intent} />
        </div>
      </div>
      <NegotiationFeedback intentId={intent.intentId} negotiation={negotiation(reports.data)} />
      {c.length > 0 && (
        <Callout tone="warn" title={`Conflict${c.length > 1 ? `s (${c.length})` : ""}`}>
          {c.map((x) => (
            <div key={x.conflictId}>{x.conflictType.replace(/_/g, " ").toLowerCase()} with {x.conflictingIntent ? <Id value={x.conflictingIntent} /> : x.conflictingExpectation ?? x.conflictingTarget ?? "—"}
              {x.recommendedSolutions ? ` · recommended: ${x.recommendedSolutions}` : ""}</div>
          ))}
        </Callout>
      )}
    </article>
  );
}
