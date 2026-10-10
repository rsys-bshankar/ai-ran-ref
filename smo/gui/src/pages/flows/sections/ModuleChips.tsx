/** The SMO modules a flow crosses, as chips (from `lib/flows.ts` FlowDef.modules). Section id `flows.modules`. */
/** The chip row. */
export function ModuleChips({ modules }: { modules: string[] }) {
  return <div className="row gap wrap" data-section="flows.modules" aria-label="Modules">{modules.map((m) => <span key={m} className="chip mono">{m}</span>)}</div>;
}
