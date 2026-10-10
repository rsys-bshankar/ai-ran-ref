/** Section `approvals.why` ("Why the rApp asks"): the rationale the rApp sent with its action and its inputs as chips (inputs reference, model
 * version, the rApp's own action id), plus the link to the decision record once the request is decided. */
import { Link } from "react-router-dom";

import type { ApprovalDetail, DecisionRecord } from "../../../api/types";
import { Card } from "../../../components/ui";

/** The rationale card; `record` is the request's decision record, when it has one. */
export function Rationale({ approval: a, record }: { approval: ApprovalDetail; record: DecisionRecord | undefined }) {
  const d = a.decision;
  const chips: [string, string | null | undefined][] = [["Inputs", d?.inputsRef], ["Model version", d?.modelVersion], ["Its action id", d?.actionId]];
  return (
    <Card section="approvals.why" title="Why the rApp asks">
      <p>{d?.rationale ?? <span className="muted">The rApp gave no rationale.</span>}</p>
      <div className="row gap wrap" aria-label="Inputs">
        {chips.filter(([, v]) => v).map(([k, v]) => <span key={k} className="chip" title={k}><span className="muted">{k}</span> <code>{v}</code></span>)}
        {chips.every(([, v]) => !v) && <span className="muted small">No inputs referenced.</span>}
      </div>
      {record && <p className="small"><Link to={`/decisions/${record.decisionId}`}>Open the decision record →</Link></p>}
    </Card>
  );
}
