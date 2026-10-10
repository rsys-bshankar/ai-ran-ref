/** The lifecycle cell of an rApp row: flow 07's five segments as a mini stepper plus the stage name, from the row's own state (no call).
 * The cell links to the flow 07 board for this instance, where every step is proved by live state. */
import { Link } from "react-router-dom";

import { MiniSteps } from "../../../kit/Timeline";
import { instanceLifecycle, LIFECYCLE_SEGMENTS } from "../data/lifecycle";

/** The cell; `instanceId` makes it a link to `/flows/07?subject=<instance>`. */
export function LifecycleCell({ state, instanceId }: { state: string | null | undefined; instanceId?: string }) {
  const lc = instanceLifecycle(state);
  const label = `Flow 07 · ${lc.stage} (${LIFECYCLE_SEGMENTS.map((s, i) => `${s}: ${lc.states[i]}`).join(", ")})`;
  const body = <span className="row gap"><MiniSteps states={lc.states} label={label} /><span className="small muted">{lc.stage}</span></span>;
  return instanceId ? <Link to={`/flows/07?subject=${instanceId}`} onClick={(e) => e.stopPropagation()} title="Open flow 07 for this instance">{body}</Link> : body;
}
