/** Section `approvals.decide` ("Your decision"): reason, Approve & write, Reject, for a request that still waits and a role allowed to decide
 * (`Can` on the approve route). Approve runs the rApp's safeguards again, then writes; the answer (the request as it now stands) is shown
 * as the result. Who decided is never sent: the BFF pins it to the signed-in user.
 *
 * Two-person approval: when the rApp's policy needs two different people, the box says whether this is the first approval (nothing is
 * written yet) or the last one, and a person who already approved has Approve disabled (they can still reject). That check compares the
 * BFF's `smo-gui:<username>` voter name with the signed-in user; RAN NF OAM refuses a second approval by the same person anyway. */
import { useState } from "react";

import { useSmoAction } from "../../../api/hooks";
import type { ApprovalDetail } from "../../../api/types";
import { useAuth } from "../../../auth/AuthContext";
import { Can, Card, Field, Id } from "../../../components/ui";
import { Callout } from "../../../kit/Callout";
import { APPROVAL_MEANING } from "../../../lib/domain";
import { APPROVALS } from "../data/queries";

/** The message after the first of two approvals: recorded, nothing written yet. */
const FIRST_OF_TWO = "Your approval is recorded: another person must approve before anything is written";

/** What the approve / reject call answered. */
interface Outcome { verb: "approve" | "reject"; body: Partial<ApprovalDetail> & { jobStatus?: string } }

/** The decision box. */
export function DecisionBox({ approval: a }: { approval: ApprovalDetail }) {
  const [reason, setReason] = useState("");
  const [outcome, setOutcome] = useState<Outcome | null>(null);
  const action = useSmoAction();
  const { me } = useAuth();
  const id = a.approvalId;
  const needed = a.requiredApprovals ?? 1;
  const given = a.approvals ?? [];
  // The BFF records a vote under `smo-gui:<username>`; comparing that name is how the box knows this user already approved (one person cannot give both approvals).
  const iApproved = me !== null && given.some((v) => v.by.trim().toLowerCase() === `smo-gui:${me.username}`.toLowerCase());
  const lastApproval = given.length + 1 >= needed;
  const decide = (verb: "approve" | "reject") => action.mutate(
    { method: "POST", path: `${APPROVALS}/${id}/${verb}`, json: { reason: reason.trim() || null },
      success: verb === "reject" ? "Rejected: nothing was written" : lastApproval ? "Approved: the change is being written" : FIRST_OF_TWO },
    { onSuccess: (body) => { setReason(""); setOutcome({ verb, body: (body ?? {}) as Outcome["body"] }); } });
  if (outcome) {
    const status = outcome.body.status ?? (outcome.verb === "approve" ? "APPROVED" : "REJECTED");
    // the first of two approvals leaves the request PENDING: say so rather than "PENDING: waiting for a decision"
    if (outcome.verb === "approve" && status === "PENDING") {
      return <div data-section="approvals.decide"><Callout tone="info" title={FIRST_OF_TWO}>No config job is made until the second approval.</Callout></div>;
    }
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
        {needed > 1 && <p className="small" role="note">{iApproved
          ? "You have approved this request. A different person must give the other approval; you can still reject it."
          : lastApproval ? "Your approval is the last one needed: approving writes the change." : "This is the first of two approvals: nothing is written until a different person approves too."}</p>}
        <Field label="Reason" hint="Optional; kept with the decision in the audit log"><input value={reason} maxLength={1000} onChange={(e) => setReason(e.target.value)} /></Field>
        <div className="row gap">
          <button type="button" className="btn danger" disabled={action.isPending} onClick={() => decide("reject")}>Reject</button>
          <button type="button" className="btn primary" disabled={action.isPending || iApproved} title={iApproved ? "You have already approved this request" : undefined} onClick={() => decide("approve")}>{action.isPending ? "…" : "Approve & write"}</button>
        </div>
      </Card>
    </Can>
  );
}
