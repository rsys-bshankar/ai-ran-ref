/** "Needs your attention" (`dashboard.attention`, handoff `Main.dc.html`, SCALE.md P4): four groups (critical alarms, pending approvals, model guard
 * breaches, SA SMOS escalations), each its newest three items and "+N more" from the summary's true total, with a link to the full list. */
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

import type { Alarm, Approval, MlmfReport, RemedialAction } from "../../../api/types";
import { Card, Id, StateBadge } from "../../../components/ui";
import { count, sum } from "../../../data/summary";
import { Badge } from "../../../kit/Badge";
import { formatCount } from "../../../kit/Kpi";
import { QueryState, type QueryLike } from "../../../kit/states";
import { formatTime } from "../../../lib/domain";
import { useBreaches, useCriticalAlarms, useDashboardSummary, useEscalations, usePendingApprovals } from "../data/queries";

/** One group: its heading with the total, up to three rows, "+N more" when the total is larger than what is shown. */
function Group<T>({ title, to, total, q, render, rowKey }: {
  title: string; to: string; total: number | null; q: QueryLike & { data?: T[] }; render: (row: T) => ReactNode; rowKey: (row: T) => string;
}) {
  const shown = q.data?.length ?? 0;
  const more = total !== null ? total - shown : 0;
  return (
    <div className="col" style={{ gap: 6 }}>
      <div className="row between"><span className="eyebrow">{title}</span><Link className="small" to={to}>{formatCount(total)} →</Link></div>
      <QueryState q={q} lines={2} empty={<p className="small muted">None.</p>}>
        <ul className="list attention">{(q.data ?? []).map((r) => <li key={rowKey(r)} className="small">{render(r)}</li>)}</ul>
        {more > 0 && <Link className="small" to={to}>+{formatCount(more)} more</Link>}
      </QueryState>
    </div>
  );
}

/** The card. Its badge is the sum of the four totals (unknown when any of them is). */
export function NeedsAttention() {
  const s = useDashboardSummary().data;
  const alarms = useCriticalAlarms();
  const approvals = usePendingApprovals();
  const breaches = useBreaches();
  const escalations = useEscalations();
  const all = sum(s, ["alarms.critical", "approvals.PENDING", "mlmfBreaches.total", "escalations.total"]);
  return (
    <Card section="dashboard.attention" title="Needs your attention" actions={<Badge tone={all ? "bad" : "ok"} plain>{formatCount(all)}</Badge>}>
      <div className="stack" style={{ gap: 14 }}>
        <Group<Alarm> title="Critical alarms" to="/alarms" total={count(s, "alarms.critical")} q={alarms} rowKey={(a) => a.alarmId} render={(a) => (
          <span><span className="sev sev-cr">critical</span> <Link to={`/elements/${encodeURIComponent(a.managedElementRef)}`}><strong>{a.managedElementRef}</strong></Link>
            <span className="muted"> · {a.specificProblem ?? a.probableCause ?? a.alarmType ?? "alarm"} · {formatTime(a.raisedAt)}</span></span>
        )} />
        <Group<Approval> title="Approvals" to="/approvals" total={count(s, "approvals.PENDING")} q={approvals} rowKey={(a) => a.approvalId} render={(a) => (
          <span><strong>{a.invokerId}</strong><span className="muted"> · {a.changeCount} change{a.changeCount === 1 ? "" : "s"} · lapses {formatTime(a.expiresAt)}</span></span>
        )} />
        <Group<MlmfReport> title="Model breaches" to="/aiml#mlmf" total={count(s, "mlmfBreaches.total")} q={breaches} rowKey={(r) => r.reportId} render={(r) => (
          <span>subscription <Id value={r.subscriptionId} /><span className="muted"> · under its floor · {formatTime(r.reportedAt)}</span></span>
        )} />
        <Group<RemedialAction> title="Escalations" to="/kpis#assurance" total={count(s, "escalations.total")} q={escalations} rowKey={(a) => a.actionId} render={(a) => (
          <span><StateBadge state={a.outcome} /> <strong>{a.actionType}</strong><span className="muted"> on monitor </span><Id value={a.monitorId} /></span>
        )} />
      </div>
    </Card>
  );
}
