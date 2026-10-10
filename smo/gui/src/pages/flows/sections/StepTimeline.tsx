/** The step timeline of a flow board: the evaluated steps of the chosen subject, drawn with `kit/Timeline` (done ✓, current ringed, failed !,
 * blocked dimmed), with the flow's own actions on the current, warned or failed step, phase headings when the flow has phases (flow 02),
 * and a progress bar. Section id `flows.steps`. */
import type { ReactNode } from "react";

import { Timeline } from "../../../kit/Timeline";
import { progress, toStepState, type FlowStep } from "../../../lib/flows";

/** The timeline of `steps` with `actions` per step id. */
export function StepTimeline({ steps, actions, alwaysActions = [] }: { steps: FlowStep[]; actions: Record<string, ReactNode>; alwaysActions?: string[] }) {
  const p = progress(steps);
  const phases = [...new Set(steps.map((s) => s.phase).filter((x): x is string => !!x))];
  const groups = phases.length ? phases.map((ph) => ({ phase: ph as string | undefined, steps: steps.filter((s) => s.phase === ph) })) : [{ phase: undefined, steps }];
  let n = 0;
  return (
    <div className="stack" data-section="flows.steps">
      <div className="flow-progress">
        <div className="bar"><span style={{ width: `${(p.done / p.total) * 100}%` }} className={p.failed ? "bad" : p.complete ? "ok" : ""} /></div>
        <span className="muted small">{p.done}/{p.total} steps{p.complete ? " — flow complete" : p.failed ? " — stopped at a failure" : ""}</span>
      </div>
      {groups.map((g) => {
        const start = n;
        n += g.steps.length;
        return (
          <div key={g.phase ?? "all"}>
            {g.phase && <div className="eyebrow">Phase · {g.phase}</div>}
            <Timeline label={g.phase ? `Steps · ${g.phase}` : "Steps"} items={g.steps.map((s, i) => ({
              key: s.id, state: toStepState(s.status),
              title: <>{phases.length ? `${start + i + 1}. ` : ""}{s.title}</>,
              meta: <span className="muted small">{s.actor}</span>,
              detail: s.detail,
              actions: (s.status === "current" || s.status === "warn" || s.status === "failed" || alwaysActions.includes(s.id)) && actions[s.id] ? actions[s.id] : undefined,
            }))} />
          </div>
        );
      })}
    </div>
  );
}
