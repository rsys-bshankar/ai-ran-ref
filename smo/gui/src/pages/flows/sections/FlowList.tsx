/** The flow catalogue (`lib/flows.ts` FLOWS) as navigation: each flow links to its board (`/flows/<id>`, keeping `?subject=` only for the
 * flow it belongs to). Only the selected flow shows a progress bar, for the subject it follows (the others would need their subjects loaded).
 * Section id `flows.list`. */
import { Link } from "react-router-dom";

import { FLOWS } from "../../../lib/flows";

/** The selected flow's progress. */
export interface ListProgress { done: number; total: number; failed: boolean; complete: boolean }

/** The list. */
export function FlowList({ selected, progress }: { selected: string; progress: ListProgress | null }) {
  return (
    <nav className="flow-list" aria-label="Call flows" data-section="flows.list">
      {FLOWS.map((f) => {
        const on = f.id === selected;
        return (
          <Link key={f.id} to={`/flows/${f.id}`} className={`flow-item ${on ? "active" : ""}`} aria-current={on ? "page" : undefined}>
            <span className="flow-num">{f.number}</span>
            <span className="flows-item-b">
              <strong>{f.title}</strong>
              <span className="muted small">{f.modules.join(" · ")}</span>
              {on && progress && (
                <span className="flow-progress" title={`${progress.done} of ${progress.total} steps`}>
                  <span className="bar"><span style={{ width: `${(progress.done / progress.total) * 100}%` }} className={progress.failed ? "bad" : progress.complete ? "ok" : ""} /></span>
                  <span className="small muted">{progress.done}/{progress.total}</span>
                </span>
              )}
            </span>
          </Link>
        );
      })}
      <p className="gap-note">Generic lifecycle flows only. 17 and 26 are phases of 02; there is no flow 05. Sample-rApp closed loops (22–25) and explanatory flows are left out on purpose.</p>
    </nav>
  );
}
