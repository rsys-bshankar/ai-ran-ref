/** The rApp detail "Recent decisions" box (SCALE.md: latest 10, then paged in Decisions): the newest ten decision records of this rApp's invoker
 * (`/ran-nf-oam/decision-records?invoker_id=&limit=10&total=false`), linking to the Decisions page filtered to it. A terminated instance has no
 * credential, so it has no decisions to show. Section id `rapp.decisions`. */
import { Link } from "react-router-dom";

import type { DecisionRecord } from "../../../api/types";
import { Card, DataTable, StateBadge } from "../../../components/ui";
import { Empty } from "../../../kit/states";
import { formatTime } from "../../../lib/domain";
import { useRecentDecisions, useSafeguards } from "../data/queries";

/** The box of instance `id`. */
export function RecentDecisions({ id }: { id: string }) {
  const sg = useSafeguards(id);
  const invoker = sg.data?.invokerId ?? null;
  const page = useRecentDecisions(invoker);
  return (
    <Card section="rapp.decisions" title="Recent decisions" sub="latest 10 of this rApp"
      actions={invoker ? <Link className="btn small" to={`/decisions?invoker=${encodeURIComponent(invoker)}`}>All, filtered to this rApp →</Link> : undefined}>
      {sg.data && !invoker ? <Empty title="No credential, so no decisions.">A terminated instance makes no changes.</Empty>
        : <DataTable<DecisionRecord> rows={page.data?.items} loading={!page.data && !page.error} error={page.error} rowKey={(r) => r.decisionId}
            empty="This rApp has made no decision yet." columns={[
              { header: "When", render: (r) => formatTime(r.occurredAt) },
              { header: "Elements", render: (r) => r.managedElements.length ? <span className="small">{r.managedElements.slice(0, 2).join(", ")}{r.managedElements.length > 2 ? ` +${r.managedElements.length - 2}` : ""}</span> : "—" },
              { header: "Why", render: (r) => <span className="small">{r.rationale ?? "—"}</span> },
              { header: "Outcome", render: (r) => <StateBadge state={r.disposition} /> },
            ]} />}
    </Card>
  );
}
