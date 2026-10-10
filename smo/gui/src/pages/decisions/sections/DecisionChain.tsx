/** Section `decisions.chain`: one decision as a six-step chain — inputs → model → rationale → config job → approval → verify — built from the
 * record's own fields (`kit/Timeline`). The record does not carry a verification result, so the last step says so instead of inventing one. */
import { Link } from "react-router-dom";

import type { DecisionRecord } from "../../../api/types";
import { Card, Id, StateBadge } from "../../../components/ui";
import { Empty } from "../../../kit/states";
import { Timeline, type TimelineItem } from "../../../kit/Timeline";
import { DISPOSITION_MEANING, formatTime } from "../../../lib/domain";

const NOT_WRITTEN = new Set(["REJECTED", "EXPIRED", "REFUSED"]);

/** The six steps of a record. */
export function decisionChain(r: DecisionRecord): TimelineItem[] {
  const notWritten = NOT_WRITTEN.has(r.disposition);
  return [
    { key: "inputs", state: r.inputsRef ? "done" : "todo", title: "Inputs", detail: r.inputsRef ?? "the rApp referenced no inputs" },
    { key: "model", state: r.modelVersion ? "done" : "todo", title: "Model", detail: r.modelVersion ?? "no model version given" },
    { key: "rationale", state: r.rationale ? "done" : "todo", title: "Rationale", detail: r.rationale ?? "none given" },
    { key: "job", state: r.jobId ? "done" : notWritten ? "fail" : "todo", title: "Config job",
      detail: r.jobId ? <>job <Id value={r.jobId} /> · {r.changeCount} change{r.changeCount === 1 ? "" : "s"} on {r.managedElements.join(", ") || "—"}</> : "none was made" },
    { key: "approval", state: r.disposition === "DIRECT" || r.disposition === "ROLLBACK" ? "done" : r.approvedBy ? "done" : notWritten ? "fail" : "todo", title: "Approval",
      detail: r.disposition === "DIRECT" ? "Autonomous: not held for approval"
        : r.approvedBy ? `approved by ${r.approvedBy}${r.decidedAt ? ` · ${formatTime(r.decidedAt)}` : ""}`
        : r.approvalId ? `${r.disposition.toLowerCase()}${r.decidedBy ? ` by ${r.decidedBy}` : ""}` : "not held for approval" },
    { key: "verify", state: "block", title: "Verify", detail: <span className="gap-note">The decision record does not carry a verification result yet.</span> },
  ];
}

/** The chain panel for the selected record. */
export function DecisionChain({ record: r }: { record: DecisionRecord | null }) {
  if (!r) return <Card section="decisions.chain" title="Decision chain"><Empty title="No decision selected.">Click a row to see why it was made.</Empty></Card>;
  return (
    <Card section="decisions.chain" title={<>Decision <Id value={r.decisionId} /></>} sub={`${r.requestedBy} · ${formatTime(r.occurredAt)}`}
      actions={<Link to={`/decisions/${r.decisionId}`}>Open the record →</Link>}>
      <div className="row gap wrap"><StateBadge state={r.disposition} /><span className="muted small">{DISPOSITION_MEANING[r.disposition]}</span></div>
      <Timeline label="Decision chain" items={decisionChain(r)} />
    </Card>
  );
}
