/** Section `safeguards.refusals` (tab "Refusals"): every time the platform refused an rApp, newest first, paged on the server and bounded in time
 * (default the last 7 days); filters are the route's own `code`, `invoker_id` and `since`. The reason is a badge with its code. */
import { useMemo, useState } from "react";

import type { RefusalCode, SafeguardRefusal } from "../../../api/types";
import { Card, Id } from "../../../components/ui";
import { Badge } from "../../../kit/Badge";
import { Segmented } from "../../../kit/Segmented";
import { ServerTable } from "../../../kit/ServerTable";
import { formatTime, REFUSAL_CODES, REFUSAL_MEANING } from "../../../lib/domain";
import { REFUSALS, refusalSince, type RefusalRange } from "../data/queries";

/** The refusal list. */
export function Refusals() {
  const [code, setCode] = useState<RefusalCode | "">("");
  const [invoker, setInvoker] = useState("");
  const [range, setRange] = useState<RefusalRange>("7d");
  const since = useMemo(() => refusalSince(range), [range]);
  return (
    <Card section="safeguards.refusals" title="Refusals, newest first" sub="every write a safeguard stopped" actions={<>
      <Segmented<RefusalRange> label="Refusal time range" value={range} onChange={setRange}
        options={[{ id: "24h", label: "24 h" }, { id: "7d", label: "7 d" }, { id: "30d", label: "30 d" }, { id: "all", label: "All" }]} />
      <select value={code} onChange={(e) => setCode(e.target.value as RefusalCode | "")} aria-label="Reason">
        <option value="">Any reason</option>
        {REFUSAL_CODES.map((c) => <option key={c} value={c}>{c}</option>)}
      </select>
      <input placeholder="rApp invoker id" value={invoker} onChange={(e) => setInvoker(e.target.value)} aria-label="Invoker id" />
    </>}>
      <p className="muted small">Every time the platform refused an rApp, whether or not anyone was watching. Repeats of the same refusal are all recorded here but announced to watchers once a minute.</p>
      <ServerTable<SafeguardRefusal> path={REFUSALS} query={{ code: code || undefined, invoker_id: invoker.trim() || undefined, since }}
        rowKey={(r) => r.refusalId} empty="No refusals recorded." columns={[
          { header: "When", render: (r) => formatTime(r.occurredAt) },
          { header: "rApp", render: (r) => <Id value={r.invokerId} /> },
          { header: "Refused because", render: (r) => <Badge tone={r.refusal === "RAPP_KILLED" ? "bad" : "warn"} title={REFUSAL_MEANING[r.refusal]}>{r.refusal}</Badge> },
          { header: "Requested by", render: (r) => r.requestedBy ?? <span className="muted">—</span> },
          { header: "Detail", render: (r) => r.detail ?? <span className="muted">—</span> },
          { header: "Announced", render: (r) => (r.announced ? "yes" : <span className="muted">no</span>) },
        ]} />
    </Card>
  );
}
