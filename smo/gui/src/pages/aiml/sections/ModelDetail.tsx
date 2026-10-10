/** Section `aiml.detail`: the selected model. Its name, version, model and runtime state, the MODEL_PIPELINE steps (`lib/domain.ts`
 * pipelineSteps, drawn with `kit/Timeline` Steps), the lifecycle buttons, Edit metadata and Delete (role-gated), and its registry facts. Reads the
 * model (MLMR) and its lifecycle row (AIMgF); the guard chart, artifacts, runtime and governance are their own sections. */
import { useState } from "react";

import { Can, Card, KeyValue, StateBadge, ActionButton } from "../../../components/ui";
import { pipelineSteps } from "../../../lib/domain";
import { Steps, type StepState } from "../../../kit/Timeline";
import { ErrorRetry, Skeleton } from "../../../kit/states";
import { mlmrModel, useModel, useModelLifecycle } from "../data/queries";
import { ModelActions } from "./ModelActions";
import { EditModel } from "./ModelForms";

/** pipelineSteps' "current" is the stepper's "now". */
const STEP: Record<string, StepState> = { done: "done", current: "now", todo: "todo" };

/** The panel; `onClose` clears the selection (also after a delete). */
export function ModelDetail({ id, onClose }: { id: string; onClose: () => void }) {
  const model = useModel(id);
  const lifecycle = useModelLifecycle(id);
  const [editing, setEditing] = useState(false);
  const m = model.data;
  const l = lifecycle.data;
  const ended = l && ["DEPRECATED", "RETIRED", "FAILED"].includes(l.modelLifecycleState);
  return (
    <Card section="aiml.detail" title={m ? <>{m.modelType} <span className="mono muted">v{m.version}</span></> : "Model"}
      sub={m?.description ?? undefined}
      actions={<button type="button" className="btn ghost small" onClick={onClose} aria-label="Close model detail">Close</button>}>
      {(model.error || lifecycle.error) && !(m && l) ? <ErrorRetry error={model.error ?? lifecycle.error} onRetry={() => { void model.refetch(); void lifecycle.refetch(); }} />
        : !(m && l) ? <Skeleton lines={5} />
          : <>
            <div className="row wrap"><StateBadge state={l.modelLifecycleState} /><span className="small muted">Runtime</span><StateBadge state={l.runtimeLifecycleState} /></div>
            <Steps label="Model lifecycle" steps={[
              ...pipelineSteps(l.modelLifecycleState).map((s) => ({ key: s.state, label: s.state, state: STEP[s.status] })),
              ...(ended ? [{ key: l.modelLifecycleState, label: l.modelLifecycleState, state: (l.modelLifecycleState === "FAILED" ? "fail" : "warn") as StepState }] : []),
            ]} />
            <div className="row between wrap">
              <div className="row gap">
                <Can method="PUT" path={mlmrModel(id)}><button type="button" className="btn small" onClick={() => setEditing(true)}>Edit metadata</button></Can>
                <ActionButton label="Delete model" tone="danger" confirm={`Delete ${m.modelType} v${m.version} with its jobs, subscriptions and artifacts?`}
                  action={{ method: "DELETE", path: mlmrModel(id), success: "Model deleted" }} onDone={onClose} />
              </div>
              <ModelActions model={m} lifecycle={l} />
            </div>
            <KeyValue items={[
              ["Model ID", <code>{m.modelId}</code>], ["Description", m.description], ["Author / owner", [m.author, m.owner].filter(Boolean).join(" / ") || null],
              ["Input → output", m.inputDataType || m.outputDataType ? `${m.inputDataType ?? "?"} → ${m.outputDataType ?? "?"}` : null],
              ["Cleared node groups", l.clearedNodeGroups.join(", ") || null], ["Artifact", m.artifactLocation],
            ]} />
          </>}
      {editing && m && <EditModel model={m} onClose={() => setEditing(false)} />}
    </Card>
  );
}
