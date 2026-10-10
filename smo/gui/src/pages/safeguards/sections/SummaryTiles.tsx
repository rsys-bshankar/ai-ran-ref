/** Section `safeguards.tiles`: rApp instances (BFF summary `instances.total`), rApps stopped at RAN NF OAM and refusals in the last 24 h — both
 * server counts (`total` of a one-row read), never counted from a list in the browser. "Near their rate limit" and "holding for approval" have
 * no server count yet, so they are not shown (README, Known limits). */
import { useMemo } from "react";

import { Card } from "../../../components/ui";
import { count } from "../../../data/summary";
import { formatCount, Kpi } from "../../../kit/Kpi";
import { refusalSince, useInstanceSummary, useRefusalCount, useStoppedCount } from "../data/queries";

/** The tiles. */
export function SummaryTiles() {
  const since = useMemo(() => refusalSince("24h") ?? "", []);
  const summary = useInstanceSummary();
  const stopped = useStoppedCount();
  const refusals = useRefusalCount(since);
  const shown = (q: { data?: { total?: number }; error?: unknown }) => (q.data ? formatCount(q.data.total ?? null) : q.error ? null : "…");
  return (
    <Card section="safeguards.tiles">
      <div className="grid g3">
        <Kpi label="rApp instances" value={summary.data ? formatCount(count(summary.data, "instances.total")) : summary.error ? null : "…"} foot="every instance, any state" />
        <Kpi label="Stopped" value={shown(stopped)} foot="writes refused until resumed" tone={(stopped.data?.total ?? 0) > 0 ? "warm" : undefined} />
        <Kpi label="Refusals · 24 h" value={shown(refusals)} foot="every write a safeguard stopped" />
      </div>
    </Card>
  );
}
