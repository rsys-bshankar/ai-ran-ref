/** Section `decisions.filters`: the list's filters, each one a query parameter of the route — rApp (invoker id), outcome, model version, a time
 * range (default the last 24 h, SCALE.md: 100k records a day need a range) and an optional "until". `?job=` / `?approval=` in the address narrow
 * the list to one config job or one approval request; then the range starts at "all", so an older record still shows. "Export CSV" downloads
 * the records of the current range, rApp and outcome (RAN NF OAM's streamed export, at most 31 days; "All" has no start, so it cannot export). */
import { Link } from "react-router-dom";

import { Card, Field, Id } from "../../../components/ui";
import { Segmented } from "../../../kit/Segmented";
import type { Query } from "../../../api/client";
import { DISPOSITIONS, exportLink, type DecisionFilter, type Range } from "../data/queries";

/** Props of {@link FilterBar}. */
export interface FilterBarProps { filter: DecisionFilter; range: Range; onChange: (patch: Partial<DecisionFilter>) => void; onRange: (r: Range) => void; query?: Query }

/** The filter bar. */
export function FilterBar({ filter, range, onChange, onRange, query }: FilterBarProps) {
  const link = query ? exportLink(query) : null;
  return (
    <Card section="decisions.filters">
      <div className="row gap wrap">
        <Field label="rApp (invoker id)"><input aria-label="Filter by rApp" value={filter.invoker} onChange={(e) => onChange({ invoker: e.target.value })} /></Field>
        <Field label="Outcome">
          <select aria-label="Filter by outcome" value={filter.disposition} onChange={(e) => onChange({ disposition: e.target.value })}>
            <option value="">All</option>
            {DISPOSITIONS.map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
        </Field>
        <Field label="Model version"><input aria-label="Filter by model version" value={filter.model} onChange={(e) => onChange({ model: e.target.value })} /></Field>
        <Field label="Since">
          <Segmented<Range> label="Time range" value={range} onChange={onRange}
            options={[{ id: "1h", label: "1 h" }, { id: "24h", label: "24 h" }, { id: "7d", label: "7 d" }, { id: "all", label: "All" }]} />
        </Field>
        <Field label="Until" hint="optional"><input type="datetime-local" aria-label="Until" value={filter.until} onChange={(e) => onChange({ until: e.target.value })} /></Field>
        {link && ("href" in link
          ? <a className="btn small" href={link.href} download title="rApp, outcome and time range apply; model version, job and approval do not">Export CSV</a>
          : <span className="small muted" role="note">{link.reason}</span>)}
      </div>
      {(filter.job || filter.approval) && (
        <p className="small">Narrowed to {filter.job ? <>job <Id value={filter.job} /></> : <>request <Id value={filter.approval} /></>}. <Link to="/decisions">Show all</Link></p>
      )}
    </Card>
  );
}
