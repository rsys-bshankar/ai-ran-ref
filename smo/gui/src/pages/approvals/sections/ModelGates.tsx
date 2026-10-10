/** Section `approvals.models` (GUI-7.3): the model gates waiting for a person, the second kind of request in the inbox. AIMgF keeps them
 * (`GET /aimgf/model-lifecycles?awaiting_decision=true`, server-paged with the true total, the rule of `lib/domain.ts` awaitsDecision): a
 * finished training or validation to approve, an emulated model to submit, a submitted one to approve or reject, an approved one to certify.
 * Each row has the decision buttons of the AI/ML page (`ModelActions`, each shown only to a role the BFF lets make that call), and a link to
 * the model there for its metrics and history. */
import { Link } from "react-router-dom";

import type { ModelLifecycle } from "../../../api/types";
import { Card, StateBadge } from "../../../components/ui";
import { ServerTable } from "../../../kit/ServerTable";
import { GATE_DECISION } from "../../../lib/domain";
import { ModelActions, useModelNames } from "../../aiml";
import { MODEL_GATES, MODEL_GATES_QUERY } from "../data/queries";

/** The tab's card. */
export function ModelGates() {
  const name = useModelNames();
  return (
    <Card section="approvals.models" title="Model gates waiting" sub="AIMgF · a model moves on only when a person decides · recorded against your GUI user">
      <ServerTable<ModelLifecycle> path={MODEL_GATES} query={MODEL_GATES_QUERY} rowKey={(l) => l.modelId}
        empty="No model waits for a decision." columns={[
          { header: "Model", render: (l) => <Link to={`/aiml?model=${encodeURIComponent(l.modelId)}#models`}>{name(l.modelId) ?? l.modelId.slice(0, 8)}</Link> },
          { header: "Stage", render: (l) => <StateBadge state={l.modelLifecycleState} /> },
          { header: "Decision", render: (l) => <span className="small">{GATE_DECISION[l.modelLifecycleState] ?? "—"}</span> },
          { header: "", render: (l) => <ModelActions model={l} lifecycle={l} /> },
        ]} />
    </Card>
  );
}
