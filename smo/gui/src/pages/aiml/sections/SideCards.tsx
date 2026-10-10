/** Sections `aiml.trainingTop` and `aiml.waiting`: the Models tab's two side cards. "Training now" lists the newest five IN_PROGRESS training
 * jobs with the true count of them and the step each reached, or its epoch progress and ETA when the runtime reports epochs; "Waiting for governance" lists the models whose next
 * step is a governance decision (approve training or validation, submit, approve, certify), from the lifecycle list the stage board already
 * read (no extra call), with their role-gated decision buttons. A click on a model selects it. */
import type { TrainingJob } from "../../../api/types";
import { Card, StateBadge } from "../../../components/ui";
import { Empty, ErrorRetry, Skeleton } from "../../../kit/states";
import { useLifecycleIndex, useModelIndex, useModelNames, useRunningTraining } from "../data/queries";
import { ModelActions } from "./ModelActions";
import { TrainingProgress } from "./TrainingTable";

const STEP: Record<TrainingJob["currentStep"], string> = { DATA_EXTRACTION: "data extraction", TRAINING: "training", TRAINED_MODEL: "trained model" };

/** "Training now": top five running jobs. */
export function TrainingNow({ onSelect, onSeeAll }: { onSelect: (id: string) => void; onSeeAll: () => void }) {
  const jobs = useRunningTraining(5);
  const name = useModelNames();
  const total = jobs.data?.total;
  return (
    <Card section="aiml.trainingTop" title="Training now" actions={<button type="button" className="btn ghost small" onClick={onSeeAll}>{total !== undefined ? `${total} running →` : "All jobs →"}</button>}>
      {jobs.error && !jobs.data ? <ErrorRetry error={jobs.error} onRetry={() => void jobs.refetch()} />
        : !jobs.data ? <Skeleton lines={2} />
          : jobs.data.items.length === 0 ? <Empty title="No training job is running." />
            : <ul className="list">
              {jobs.data.items.map((j) => (
                <li key={j.trainingJobId} className="row between">
                  {j.modelId
                    ? <button type="button" className="btn ghost small" onClick={() => onSelect(j.modelId!)}>{name(j.modelId) ?? j.modelId.slice(0, 8)}</button>
                    : <span className="small">group {j.modelCoordinationGroupId?.slice(0, 8)}</span>}
                  {j.epoch != null && j.totalEpochs ? <TrainingProgress job={j} /> : <span className="xs muted">{j.steps ? `step: ${STEP[j.currentStep]}` : "—"}</span>}
                  <StateBadge state={j.status} />
                </li>
              ))}
            </ul>}
    </Card>
  );
}

/** The states whose next step is a governance decision. */
const WAITING = new Set(["TRAINED", "VALIDATED", "EMULATED", "PENDING_APPROVAL", "APPROVED"]);

/** "Waiting for governance": up to five models, with the count. */
export function WaitingForGovernance({ onSelect }: { onSelect: (id: string) => void }) {
  const lifecycles = useLifecycleIndex();
  const models = useModelIndex();
  const waiting = (lifecycles.data?.items ?? []).filter((l) => WAITING.has(l.modelLifecycleState)
    && !(l.modelLifecycleState === "TRAINED" && l.trainingApproved) && !(l.modelLifecycleState === "VALIDATED" && l.validationApproved));
  return (
    <Card section="aiml.waiting" title="Waiting for governance" sub={lifecycles.data ? `${waiting.length} model${waiting.length === 1 ? "" : "s"}` : undefined}>
      {lifecycles.error && !lifecycles.data ? <ErrorRetry error={lifecycles.error} onRetry={() => void lifecycles.refetch()} />
        : !lifecycles.data ? <Skeleton lines={2} />
          : waiting.length === 0 ? <Empty title="Nothing waits for a decision." />
            : <ul className="list">
              {waiting.slice(0, 5).map((l) => {
                const m = models.data?.items.find((x) => x.modelId === l.modelId);
                return (
                  <li key={l.modelId} className="col">
                    <div className="row between">
                      <button type="button" className="btn ghost small" onClick={() => onSelect(l.modelId)}>{m ? `${m.modelType} ${m.version}` : l.modelId.slice(0, 8)}</button>
                      <StateBadge state={l.modelLifecycleState} />
                    </div>
                    {m && <ModelActions model={m} lifecycle={l} />}
                  </li>
                );
              })}
            </ul>}
    </Card>
  );
}
