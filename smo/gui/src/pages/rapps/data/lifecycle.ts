/** The rApps table's lifecycle column (SCALE.md, rApps: "a 5-segment mini stepper for flow 07, plus the stage name"): a pure mapping from the
 * instance state in the same row to five segments of call flow 07, so the column costs no extra call. Used by `sections/InstanceTable.tsx`,
 * `sections/Rollouts.tsx` and `sections/PinnedAttention.tsx`. */
import type { StepState } from "../../../kit/Timeline";

/** The five segments, in order: deployed, running and reporting, fault and recover, upgrade, terminated. */
export const LIFECYCLE_SEGMENTS = ["Deploy", "Run", "Fault / recover", "Upgrade", "Terminate"] as const;

/** One row's lifecycle: the segment states and the stage name shown next to them. */
export interface Lifecycle { states: StepState[]; stage: string }

/** The lifecycle of an instance in `state` (an unknown state reads as not started, never as done). */
export function instanceLifecycle(state: string | null | undefined): Lifecycle {
  switch (state) {
    case "DEPLOYING": return { states: ["now", "todo", "todo", "todo", "todo"], stage: "Bootstrap" };
    case "RUNNING": return { states: ["done", "now", "todo", "todo", "todo"], stage: "Running" };
    case "FAULTED": return { states: ["done", "done", "fail", "todo", "todo"], stage: "Faulted · recover" };
    case "UPGRADING": return { states: ["done", "done", "done", "now", "todo"], stage: "Upgrading" };
    case "UNDEPLOYED": return { states: ["done", "done", "done", "done", "done"], stage: "Terminated" };
    default: return { states: ["todo", "todo", "todo", "todo", "todo"], stage: state ?? "unknown" };
  }
}
