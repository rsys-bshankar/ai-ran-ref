/** Section `aiml.groups` (Coordination tab): MLMR model coordination groups as a server-paged table with a role-gated "Retrain group", and the
 * role-gated form that creates one from two or more models (`POST /mlmr/coordination-groups`). A guard-KPI breach on any PROMOTED member
 * retrains every PROMOTED member. */
import { useState } from "react";

import { useSmoAction } from "../../../api/hooks";
import type { CoordinationGroup } from "../../../api/types";
import { ActionButton, Can, Card, Field, Id } from "../../../components/ui";
import { splitList } from "../../../lib/domain";
import { ServerTable } from "../../../kit/ServerTable";
import { GROUPS, TRAINING_JOBS, useLifecycleIndex, useModelIndex, useModelNames } from "../data/queries";

/** The tab: form (role-gated) and table. */
export function CoordinationGroups() {
  const modelName = useModelNames();
  return (
    <>
      <Can method="POST" path={GROUPS}><NewGroup /></Can>
      <Card section="aiml.groups" title="Coordination groups" actions={<span className="muted small">A guard-KPI breach on any PROMOTED member retrains every PROMOTED member</span>}>
        <ServerTable<CoordinationGroup> path={GROUPS} rowKey={(g) => g.groupId} empty="No coordination groups." columns={[
          { header: "Group", render: (g) => <Id value={g.groupId} /> },
          { header: "Members", render: (g) => g.memberModelIds.map((id) => modelName(id) ?? id.slice(0, 8)).join(", ") },
          { header: "Use cases", render: (g) => g.memberUseCases.join(", ") || "—" },
          { header: "Propagation", render: (g) => g.retrainPropagation },
          { header: "", className: "actions", render: (g) => <ActionButton label="Retrain group" action={{ method: "POST", path: TRAINING_JOBS, json: { modelCoordinationGroupId: g.groupId, producerId: "smo-gui" }, success: "Group training job started" }} /> },
        ]} />
      </Card>
    </>
  );
}

/** The create form: members (two or more), use cases, retrain propagation. */
function NewGroup() {
  const models = useModelIndex();
  const lifecycles = useLifecycleIndex();
  const [members, setMembers] = useState<string[]>([]);
  const [useCases, setUseCases] = useState("");
  const [propagation, setPropagation] = useState("ANY_MEMBER_TRIGGERS");
  const action = useSmoAction();
  const stateOf = (id: string) => lifecycles.data?.items.find((l) => l.modelId === id)?.modelLifecycleState ?? "REGISTERED";
  const list = models.data?.items ?? [];
  return (
    <Card section="aiml.newGroup" title="New coordination group">
      <form className="form inline" onSubmit={(e) => {
        e.preventDefault();
        action.mutate({ method: "POST", path: GROUPS, json: { memberModelIds: members, memberUseCases: splitList(useCases), retrainPropagation: propagation }, success: "Coordination group created" },
          { onSuccess: () => setMembers([]) });
      }}>
        <Field label="Member models" hint={members.length === 1 ? <span className="text-bad">Pick at least 2 models</span> : "Ctrl/Cmd-click to pick 2 or more"}>
          <select multiple value={members} onChange={(e) => setMembers([...e.target.selectedOptions].map((o) => o.value))} size={Math.min(5, Math.max(2, list.length || 2))}>
            {list.map((m) => <option key={m.modelId} value={m.modelId}>{m.modelType} {m.version} ({stateOf(m.modelId)})</option>)}
          </select>
        </Field>
        <Field label="Use cases"><input value={useCases} onChange={(e) => setUseCases(e.target.value)} placeholder="energy-saving, mobility" /></Field>
        <Field label="Retrain propagation"><select value={propagation} onChange={(e) => setPropagation(e.target.value)}><option>ANY_MEMBER_TRIGGERS</option><option>MAJORITY_TRIGGERS</option></select></Field>
        <button type="submit" className="btn primary" disabled={members.length < 2 || action.isPending}>Create</button>
      </form>
    </Card>
  );
}
