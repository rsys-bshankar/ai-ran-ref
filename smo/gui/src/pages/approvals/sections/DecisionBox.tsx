/** Section `approvals.decide` ("Your decision"): reason, Approve & write, Reject, for a request that still waits and a role allowed to decide
 * (`Can` on the approve route). Approve runs the rApp's safeguards again, then writes; the answer (the request as it now stands) is shown
 * as the result. Who decided is never sent: the BFF pins it to the signed-in user. */
import { useState } from "react";

import { useSmoAction } from "../../../api/hooks";
import type { ApprovalDetail } from "../../../api/types";
import { Can, Card, Field, Id } from "../../../components/ui";
import { Callout } from "../../../kit/Callout";
import { APPROVAL_MEANING } from "../../../lib/domain";
import { APPROVALS } from "../data/queries";

/** What the approve / reject call answered. */
interface Outcome { verb: "approve" | "reject"; body: Partial<ApprovalDetail> & { jobStatus?: string } }

/** The decision box. */
export function DecisionBox({ approval: a }: { approval: ApprovalDetail }) {
  const [reason, setReason] = useState("");
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const action = useSmoAction();
  const id = a.approvalId;
  const decide = (verb: "approve" | "reject") => action.mutate(
    { method: "POST", path: `${APPROVALS}/${id}/${verb}`, json: { reason: reason.trim() || null }, success: verb === "approve" ? "Approved: the change is being written" : "Rejected: nothing was written" },
    { onSuccess: (body) => { setReason(""); setOutcome({ verb, body: (body ?? {}) as Outcome["body"] }); } });
  if (outcome) {
    const status = outcome.body.status ?? (outcome.verb === "approve" ? "APPROVED" : "REJECTED");
    return (
      <div data-section="approvals.decide">
        <Callout tone={status === "APPROVED" ? "volt" : "warn"} title={`${status}: ${APPROVAL_MEANING[status] ?? ""}`}>
          {outcome.body.jobId ? <>Config job <Id value={outcome.body.jobId} />{outcome.body.jobStatus && <> · {outcome.body.jobStatus}</>}</> : "No config job was made."}
        </Callout>
      </div>
    );
  }
  if (a.status !== "PENDING") return null;
  return (
    <Can method="POST" path={`${APPROVALS}/${id}/approve`}>
      <Card section="approvals.decide" title="Your decision">
        <p className="muted small">Approving checks the rApp's safeguards again (it may have been stopped or reached a limit while this waited), then writes exactly the changes above. It is recorded under your name.</p>
        <Field label="Reason" hint="Optional; kept with the decision in the audit log"><input value={reason} maxLength={1000} onChange={(e) => setReason(e.target.value)} /></Field>
        <div className="row gap">
          <button type="button" className="btn danger" disabled={action.isPending} onClick={() => decide("reject")}>Reject</button>
          <button type="button" className="btn primary" disabled={action.isPending} onClick={() => decide("approve")}>{action.isPending ? "…" : "Approve & write"}</button>
        </div>
      </Card>
    </Can>
  );
}
