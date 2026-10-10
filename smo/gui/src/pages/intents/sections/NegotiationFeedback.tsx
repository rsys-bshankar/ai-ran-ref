/** The negotiation-feedback box of one intent card (feature 10, TS 28.312 IntentFulfilmentNegotiationFeedback): the outcomes the handler
 * offered in its newest negotiation report, the feedback already given, and "Satisfied?" buttons that answer an outcome with a
 * consumerSatisfactionIndex (`POST /intent-service/intents/{id}/negotiation-feedback`). The BFF's permission table allows it to an operator and up
 * (GUI-9.7); a viewer sees the offered outcomes read-only. */
import { useState } from "react";

import { useAuth } from "../../../auth/AuthContext";
import { ActionButton } from "../../../components/ui";
import type { Negotiation } from "../data/intent";
import { feedbackPath } from "../data/queries";

/** The satisfaction choices and the index each sends (TS 28.312 leaves the scale to the consumer; 0–100 here). */
export const SATISFACTION = [{ label: "Not enough", value: 30 }, { label: "Acceptable", value: 70 }, { label: "Fully met", value: 100 }] as const;

/** The box; nothing renders for an intent without a negotiation report. */
export function NegotiationFeedback({ intentId, negotiation }: { intentId: string; negotiation: Negotiation | null }) {
  const { can } = useAuth();
  const [outcome, setOutcome] = useState<number | null>(null);
  if (!negotiation || negotiation.outcomes.length === 0) return null;
  const chosen = outcome ?? negotiation.outcomes[0].possibleIntentOutcomeId;
  const allowed = can("POST", feedbackPath(intentId));
  const given = negotiation.feedback;
  return (
    <div className="inset col" data-part="negotiation">
      <span className="xs muted">Your feedback on the handler's outcome (negotiation feedback)</span>
      {given?.referredIntentOutcomeId !== undefined && (
        <span className="small">Sent: outcome {given.referredIntentOutcomeId}{given.consumerSatisfactionIndex !== undefined ? ` · satisfaction ${given.consumerSatisfactionIndex}` : ""}</span>
      )}
      {allowed ? (
        <div className="row wrap">
          <select value={chosen} onChange={(e) => setOutcome(Number(e.target.value))} aria-label="Intent outcome">
            {negotiation.outcomes.map((o) => <option key={o.possibleIntentOutcomeId} value={o.possibleIntentOutcomeId}>outcome {o.possibleIntentOutcomeId} · {o.intentFulfilmentInfo.fulfilmentStatus}</option>)}
          </select>
          <span className="small">Satisfied?</span>
          {SATISFACTION.map((s) => (
            <ActionButton key={s.value} label={s.label} action={{ method: "POST", path: feedbackPath(intentId), json: { referredIntentOutcomeId: chosen, consumerSatisfactionIndex: s.value },
              success: `Feedback sent · satisfaction ${s.value} on outcome ${chosen}` }} />
          ))}
        </div>
      ) : (
        <span className="small">{negotiation.outcomes.length} outcome{negotiation.outcomes.length === 1 ? "" : "s"} offered: {negotiation.outcomes.map((o) => `${o.possibleIntentOutcomeId} (${o.intentFulfilmentInfo.fulfilmentStatus})`).join(", ")}
          <span className="gap-note"> · Sending feedback needs the operator role.</span></span>
      )}
    </div>
  );
}
