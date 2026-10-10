/** Section `alarms.table`: the RAN NF alarm list (O1 FaultMnS), paged on the server (SCALE.md P1). Severity, managed element and managed
 * function are the route's own query parameters; the managed element starts from `?me=` in the address (the global search links
 * `/alarms?me=X`). Ack state and "show cleared" have no route parameter, so they narrow the rows of the current page only and say how many
 * they hid. Rows are sorted most severe, then newest, within the page. A row click selects it for the detail panel. */
import { useEffect, useMemo, useState } from "react";

import type { Alarm } from "../../../api/types";
import { Card, DataTable, SeverityChip, StateBadge } from "../../../components/ui";
import { Pager } from "../../../kit/Pager";
import { Stale } from "../../../kit/states";
import { formatTime, SEVERITIES, sortAlarms } from "../../../lib/domain";
import { usePreferences } from "../../../shell/ThemeProvider";
import { useRanAlarmPage } from "../data/queries";
import { AlarmActions } from "./AlarmActions";

/** Props of {@link AlarmTable}: the severity filter is shared with the tiles; the selection with the detail panel. */
export interface AlarmTableProps {
  severity: string; onSeverity: (s: string) => void; initialElement: string;
  selectedId: string | null; onSelect: (a: Alarm | null) => void;
}

/** The table, its filters and its pager. */
export function AlarmTable({ severity, onSeverity, initialElement, selectedId, onSelect }: AlarmTableProps) {
  const { prefs } = usePreferences();
  const [me, setMe] = useState(initialElement);
  const [mf, setMf] = useState("");
  const [ack, setAck] = useState("");
  const [showCleared, setShowCleared] = useState(false);
  const [limit, setLimit] = useState<number>(prefs.rowsPerPage);
  const [offset, setOffset] = useState(0);
  useEffect(() => { setMe(initialElement); }, [initialElement]);
  useEffect(() => { setOffset(0); }, [severity, me, mf]);
  const page = useRanAlarmPage({ severity, managedElement: me, managedFunction: mf }, limit, offset);
  const items = page.data?.items;
  const rows = useMemo(() => items && sortAlarms(items.filter((a) =>
    (showCleared || severity === "cleared" || a.severity !== "cleared") && (!ack || a.ackState === ack))), [items, showCleared, severity, ack]);
  useEffect(() => {
    const fresh = selectedId ? items?.find((a) => a.alarmId === selectedId) : undefined;
    if (fresh) onSelect(fresh);
  }, [items]); // eslint-disable-line react-hooks/exhaustive-deps
  const hidden = items && rows ? items.length - rows.length : 0;
  return (
    <Card section="alarms.table" title="RAN NF alarms" sub="O1 FaultMnS · acknowledge and clear are recorded against your GUI user" actions={
      <div className="row gap wrap alarms-filters">
        <input value={me} onChange={(e) => setMe(e.target.value)} placeholder="Managed element" aria-label="Managed element" />
        <input value={mf} onChange={(e) => setMf(e.target.value)} placeholder="Managed function" aria-label="Managed function" />
        <select value={severity} onChange={(e) => onSeverity(e.target.value)} aria-label="Severity">
          <option value="">All severities</option>{[...SEVERITIES, "cleared"].map((s) => <option key={s}>{s}</option>)}
        </select>
        <select value={ack} onChange={(e) => setAck(e.target.value)} aria-label="Ack state" title="Narrows the rows of this page">
          <option value="">Any ack state</option><option>UNACKNOWLEDGED</option><option>ACKNOWLEDGED</option>
        </select>
        <label className="check"><input type="checkbox" checked={showCleared} onChange={(e) => setShowCleared(e.target.checked)} /> show cleared</label>
      </div>}>
      <DataTable rows={rows} loading={page.isLoading} error={page.data ? undefined : page.error} rowKey={(a) => a.alarmId} empty="No alarms match."
        onRowClick={(a) => onSelect(a)} selectedKey={selectedId} columns={[
          { header: "Severity", render: (a) => <SeverityChip severity={a.severity} /> },
          { header: "Managed element", render: (a) => <><strong>{a.managedElementRef}</strong>{a.managedFunctionRef && <> · <code className="small">{a.managedFunctionRef}</code></>}<div className="muted small">{a.sourceAlarmId}</div></> },
          { header: "Probable cause", render: (a) => a.probableCause ?? <span className="muted">—</span> },
          { header: "Specific problem", render: (a) => a.specificProblem ?? <span className="muted">—</span> },
          { header: "Type", render: (a) => a.alarmType ?? <span className="muted">—</span> },
          { header: "Raised", render: (a) => formatTime(a.raisedAt) },
          { header: "Ack", render: (a) => <StateBadge state={a.ackState} /> },
          { header: "", className: "actions", render: (a) => <AlarmActions alarm={a} /> },
        ]} />
      {hidden > 0 && <p className="small muted" data-hidden-rows={hidden}>{hidden} alarm{hidden === 1 ? "" : "s"} of this page hidden by the ack-state or cleared filter (they apply to the page shown, not to the server's list).</p>}
      {page.error && page.data && <div className="error-box" role="alert">Refresh failed: {page.error.message} <Stale updatedAt={page.dataUpdatedAt} after={0} /></div>}
      {page.data && <Pager offset={offset} limit={limit} shown={page.data.items.length} total={page.data.total} hasMore={page.data.hasMore}
        onOffset={setOffset} onLimit={(l) => { setLimit(l); setOffset(0); }} />}
    </Card>
  );
}
