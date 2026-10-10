/** Section `decisions.table`: the decision records matching the filter bar and the global scope, newest first, paged by keyset (GUI-9.5b,
 * `kit/KeysetTable`: the route's `after` cursor and `nextCursor`, so a deep page of a large range costs the same as the first; no count). A row
 * click selects it for the chain panel; the first row is selected when nothing is. The time links to the record's own page. */
import { useCallback } from "react";
import { Link } from "react-router-dom";

import type { Query } from "../../../api/client";
import type { DecisionRecord } from "../../../api/types";
import { Card, Id, StateBadge } from "../../../components/ui";
import { KeysetTable } from "../../../kit/KeysetTable";
import { DISPOSITION_MEANING, formatTime } from "../../../lib/domain";
import { DECISION_RECORDS } from "../data/queries";

/** Props of {@link DecisionTable}. */
export interface DecisionTableProps { query: Query; selected: DecisionRecord | null; onSelect: (r: DecisionRecord) => void }

/** The table. */
export function DecisionTable({ query, selected, onSelect }: DecisionTableProps) {
  const onRows = useCallback((rows: DecisionRecord[]) => {
    const fresh = selected ? rows.find((r) => r.decisionId === selected.decisionId) : undefined;
    if (fresh) onSelect(fresh);
    else if (!selected && rows[0]) onSelect(rows[0]);
  }, [selected?.decisionId]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Card section="decisions.table" title="Decision records" sub="newest first · paged on the server by cursor">
      <KeysetTable<DecisionRecord> path={DECISION_RECORDS} query={query} rowKey={(r) => r.decisionId}
        onRowClick={onSelect} selectedKey={selected?.decisionId ?? null} onRows={onRows}
        empty="No decision has been recorded for these filters." columns={[
          { header: "When", render: (r) => <Link to={`/decisions/${r.decisionId}`} onClick={(e) => e.stopPropagation()}>{formatTime(r.occurredAt)}</Link> },
          { header: "rApp", render: (r) => <Id value={r.invokerId} /> },
          { header: "Outcome", render: (r) => <span title={DISPOSITION_MEANING[r.disposition]}><StateBadge state={r.disposition} /></span> },
          { header: "Model", render: (r) => r.modelVersion ?? <span className="muted">—</span> },
          { header: "Rationale", render: (r) => <span className="small">{r.rationale ?? <span className="muted">none given</span>}</span> },
          { header: "Changes", render: (r) => r.changeCount },
          { header: "Approved by", render: (r) => r.approvedBy ?? <span className="muted">—</span> },
        ]} />
    </Card>
  );
}
