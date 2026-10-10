/** The audit log (`admin.audit`, handoff `Admin.dc.html`): every mutating call the BFF proxied (allowed or denied) plus sign-ins and user
 * administration, newest first, paged on the server (`GET /api/admin/audit?username&action&limit&offset`) with filters by user and event. Each row
 * carries an outcome badge: the HTTP status of a proxied call, or the word for a refused sign-in or a denied call. Time range and export are
 * ⚠ gaps: the route takes no time bounds and has no export job. */
import { useState } from "react";

import type { AuditEntry } from "../../../api/types";
import { Card, DataTable } from "../../../components/ui";
import { Badge, type Tone } from "../../../kit/Badge";
import { Pager } from "../../../kit/Pager";
import { Stale } from "../../../kit/states";
import { formatTime } from "../../../lib/domain";
import { usePreferences } from "../../../shell/ThemeProvider";
import { AUDIT_ACTIONS, useAuditPage } from "../data/queries";

const REFUSED = ["DENIED", "LOGIN_FAILED", "LOGIN_LOCKED", "LOGIN_REFUSED"];

/** The outcome of an entry: tone and text. A status code wins (a proxied call); otherwise a refused or denied event is "refused", the rest "ok". */
export function auditOutcome(e: Pick<AuditEntry, "action" | "statusCode">): { tone: Tone; text: string } {
  if (e.statusCode !== null && e.statusCode !== undefined) return { tone: e.statusCode >= 400 ? "bad" : e.statusCode >= 300 ? "warn" : "ok", text: String(e.statusCode) };
  if (REFUSED.includes(e.action)) return { tone: "bad", text: e.action === "DENIED" ? "denied" : "refused" };
  return { tone: "ok", text: "ok" };
}

/** The filters, the table and the pager. */
export function AuditLog() {
  const { prefs } = usePreferences();
  const [username, setUsername] = useState("");
  const [action, setAction] = useState("");
  const [limit, setLimit] = useState<number>(prefs.rowsPerPage);
  const [offset, setOffset] = useState(0);
  const page = useAuditPage({ username: username.trim(), action, limit, offset });
  return (
    <Card section="admin.audit" title="Audit log" sub="every mutating call, newest first" actions={<>
      <input placeholder="User" value={username} onChange={(e) => { setUsername(e.target.value); setOffset(0); }} aria-label="Filter by user" />
      <select value={action} onChange={(e) => { setAction(e.target.value); setOffset(0); }} aria-label="Filter by action">
        <option value="">All actions</option>
        {AUDIT_ACTIONS.map((a) => <option key={a}>{a}</option>)}
      </select>
    </>}>
      <p className="muted small">Append-only. Every mutating call the BFF proxies (allowed or denied) plus sign-ins and user administration.</p>
      <DataTable rows={page.data?.items} loading={page.isLoading} error={page.data ? undefined : page.error} rowKey={(e) => String(e.id)} empty="No entries." columns={[
        { header: "When", render: (e) => <span className="mono small">{formatTime(e.at)}</span> },
        { header: "User", render: (e) => <>{e.username ?? "—"}{e.role && <span className="muted small"> ({e.role})</span>}</> },
        { header: "Event", render: (e) => <code className="small">{e.action}</code> },
        { header: "Call", render: (e) => e.method ? <code className="small">{e.method} {e.path}</code> : <span className="muted">—</span> },
        { header: "Detail", render: (e) => <span className="small">{e.detail ?? ""}</span> },
        { header: "Outcome", render: (e) => { const o = auditOutcome(e); return <Badge tone={o.tone}>{o.text}</Badge>; } },
      ]} />
      {page.error && page.data && <div className="error-box" role="alert">Refresh failed: {page.error.message} <Stale updatedAt={page.dataUpdatedAt} after={0} /></div>}
      {page.data && (
        <Pager offset={offset} limit={limit} shown={page.data.items.length} total={page.data.total} hasMore={page.data.hasMore}
          onOffset={setOffset} onLimit={(l) => { setLimit(l); setOffset(0); }} />
      )}
      <p className="gap-note">No time-range filter or export yet: the audit route takes no time bounds and has no export job.</p>
    </Card>
  );
}
