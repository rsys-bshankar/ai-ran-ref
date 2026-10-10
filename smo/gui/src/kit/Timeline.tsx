/** A vertical timeline (`.tl`) and a horizontal stepper (`.steps`). The lifecycle flows, rApp detail, the decision chain and the
 * config-job waves all draw through these two, so a step's state reads the same everywhere. */
import type { ReactNode } from "react";

/** A step's state: done (green), now (accent ring), fail (red), warn (amber), block (dimmed, not reachable yet), todo. */
export type StepState = "done" | "now" | "fail" | "warn" | "block" | "todo";

/** One timeline row. */
export interface TimelineItem { key: string; state: StepState; title: ReactNode; meta?: ReactNode; detail?: ReactNode; actions?: ReactNode }

/** The vertical timeline; each row's marker is its 1-based number, or ✓ / ! for done and failed rows. */
export function Timeline({ items, label }: { items: TimelineItem[]; label?: string }) {
  return (
    <ol className="tl" aria-label={label}>
      {items.map((it, i) => (
        <li key={it.key} className={`tl-i ${it.state}`} aria-current={it.state === "now" ? "step" : undefined}>
          <span className="tl-n" aria-hidden>{it.state === "done" ? "✓" : it.state === "fail" ? "!" : i + 1}</span>
          <div className="tl-b">
            <div className="row wrap"><strong>{it.title}</strong>{it.meta}</div>
            {it.detail && <div className="small muted">{it.detail}</div>}
            {it.actions && <div className="tl-action">{it.actions}</div>}
          </div>
        </li>
      ))}
    </ol>
  );
}

/** The horizontal stepper. */
export function Steps({ steps, label }: { steps: { key: string; label: ReactNode; state: StepState }[]; label?: string }) {
  return (
    <ol className="steps" aria-label={label}>
      {steps.map((s) => <li key={s.key} className={`step ${s.state === "todo" || s.state === "block" ? "" : s.state}`} aria-current={s.state === "now" ? "step" : undefined}><i />{s.label}</li>)}
    </ol>
  );
}

/** A five-segment mini stepper for a table cell (the rApps lifecycle column). */
export function MiniSteps({ states, label }: { states: StepState[]; label: string }) {
  return <span className="ministeps" role="img" aria-label={label}>{states.map((s, i) => <i key={i} className={s} />)}</span>;
}
