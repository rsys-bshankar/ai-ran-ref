/** The role-gated lifecycle buttons of one model, shared by the model table, the detail panel and the runtime box: the next ModelLifecycle
 * step (`lib/domain.ts` modelActions: request a job, complete it through its job route, or `advance` a governance event) and the RuntimeLifecycle
 * actions. Every button is an `ActionButton`, so it renders only when the BFF's permission table lets the role make that exact call. */
import type { Model, ModelLifecycle } from "../../../api/types";
import { CompleteJobButton } from "../../../components/CompleteJobButton";
import { ActionButton } from "../../../components/ui";
import { modelActions, runtimeActions } from "../../../lib/domain";
import { TRAINING_JOBS, aimgfModel } from "../data/queries";

/** The lifecycle row AIMgF answers for a model it has never touched. */
export const REGISTERED_LIFECYCLE: ModelLifecycle = {
  modelId: "", modelLifecycleState: "REGISTERED", runtimeLifecycleState: "NOT_DEPLOYED",
  trainingJobId: null, clearedNodeGroups: [], nfDeploymentDescriptorId: null, nfDeploymentId: null,
  trainingApproved: false, validationApproved: false,
};

/** The model-lifecycle buttons legal from the model's state. DEPRECATE and RETIRE are terminal and ask first. */
export function ModelActions({ model, lifecycle }: { model: Model; lifecycle: ModelLifecycle }) {
  const base = aimgfModel(model.modelId);
  return (
    <div className="row gap end">
      {modelActions(lifecycle.modelLifecycleState, { trainingApproved: lifecycle.trainingApproved, validationApproved: lifecycle.validationApproved }).map((a) => {
        if (a.kind === "train") return <ActionButton key="train" label={a.label} tone="primary" action={{ method: "POST", path: TRAINING_JOBS, json: { modelId: model.modelId, producerId: "smo-gui" }, success: `${a.label}: training job started` }} />;
        if (a.kind === "validate") return <ActionButton key="validate" label={a.label} tone="primary" action={{ method: "POST", path: "/aimgf/validation-jobs", json: { modelId: model.modelId, producerId: "smo-gui" }, success: "Validation job started" }} />;
        if (a.kind === "emulate") return <ActionButton key="emulate" label={a.label} tone="primary" action={{ method: "POST", path: "/aimgf/emulation-jobs", json: { modelId: model.modelId, producerId: "smo-gui" }, success: "Emulation job started" }} />;
        if (a.kind === "complete") return <CompleteJobButton key={`complete-${a.stage}`} modelId={model.modelId} stage={a.stage} label={a.label} trainingJobId={lifecycle.trainingJobId} />;
        const destructive = a.event === "DEPRECATE" || a.event === "RETIRE" || a.event === "REJECT";
        return <ActionButton key={a.event} label={a.label} tone={destructive ? "danger" : "primary"}
          confirm={a.event === "DEPRECATE" || a.event === "RETIRE" ? `${a.label} this model? This is terminal.` : undefined}
          action={{ method: "POST", path: `${base}/advance`, query: a.governance ? { event: a.event, decided_by: "smo-gui" } : { event: a.event }, success: `${a.event} → done` }} />;
      })}
    </div>
  );
}

/** The runtime buttons legal from the RuntimeLifecycle state; terminate asks first. */
export function RuntimeActions({ modelId, lifecycle }: { modelId: string; lifecycle: ModelLifecycle }) {
  const base = aimgfModel(modelId);
  return (
    <div className="row gap end">
      {runtimeActions(lifecycle.runtimeLifecycleState).map((a) => (
        <ActionButton key={a.action} label={a.label} tone={a.action === "terminate" ? "danger" : "primary"}
          confirm={a.action === "terminate" ? "Terminate this model's runtime?" : undefined}
          action={{ method: "POST", path: `${base}/runtime/${a.action}`, success: `Runtime ${a.action}: done` }} />
      ))}
    </div>
  );
}
