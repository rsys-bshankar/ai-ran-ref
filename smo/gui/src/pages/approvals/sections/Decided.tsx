/** Section `approvals.decided` (tab "Decided"): requests that were approved, rejected, refused or lapsed, newest first, paged on the server. A
 * status picked here is the route's `status` parameter; with none, the page of all requests is shown without the pending ones (the route has no
 * "not pending" filter), and the section says so. A row opens the request in a drawer. */
import { useState } from "react";

import type { Approval } from "../../../api/types";
import { Card, DataTable, Id, StateBadge } from "../../../components/ui";
import { Pager } from "../../../kit/Pager";
import { APPROVAL_MEANING, describeElements, formatTime } from "../../../lib/domain";
import { usePreferences } from "../../../shell/ThemeProvider";
import { DECIDED_STATUSES, useDecided } from "../data/queries";
import { ApprovalDrawer } from "./Detail";

/** The decided list. */
export function Decided() {
  const { prefs } = usePreferences();
  const [status, setStatus] = useState("");
  const [limit, setLimit] = useState<number>(prefs.rowsPerPage);
  const [offset, setOffset] = useState(0);
  const decided = useDecided(limit, offset, status);
  const [open, setOpen] = useState<string | null>(null);
  const rows = decided.data?.items.filter((a) => a.status !== "PENDING");
  return (
    <Card section="approvals.decided" title="Decided and lapsed" actions={
      <select value={status} aria-label="Outcome" onChange={(e) => { setStatus(e.target.value); setOffset(0); }}>
        <option value="">Every outcome</option>{DECIDED_STATUSES.map((s) => <option key={s}>{s}</option>)}
      </select>}>
      {!status && <p className="muted small">Pending requests on this page are left out (they are in the Waiting tab).</p>}
      <DataTable rows={rows} rowKey={(a) => a.approvalId} error={decided.error} loading={decided.isLoading} empty="No request has been decided yet." onRowClick={(a) => setOpen(a.approvalId)} columns={[
        { header: "rApp", render: (a: Approval) => <><Id value={a.invokerId} /> <span className="muted small">{a.requestedBy}</span></> },
        { header: "Changes", render: (a) => `${a.changeCount} on ${describeElements(a.managedElements)}` },
        { header: "Outcome", render: (a) => <span title={APPROVAL_MEANING[a.status]}><StateBadge state={a.status} /></span> },
        { header: "By", render: (a) => a.decidedBy ?? "—" },
        { header: "When", render: (a) => formatTime(a.decidedAt) },
        { header: "Reason", render: (a) => a.decisionReason ?? a.refusalCode ?? <span className="muted">—</span> },
      ]} />
      {decided.data && <Pager offset={offset} limit={limit} shown={decided.data.items.length} hasMore={decided.data.hasMore}
        onOffset={setOffset} onLimit={(l) => { setLimit(l); setOffset(0); }} />}
      {open && <ApprovalDrawer id={open} onClose={() => setOpen(null)} />}
    </Card>
  );
}
