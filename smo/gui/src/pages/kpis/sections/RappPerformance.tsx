/** Section `kpis.rapp` (rApp performance tab): what one rApp instance reports about itself (`POST /rapp-mgmt/instances/{id}/performance`),
 * as one sparkline per numeric metric and the report table, for the instance picked (the first RUNNING one by default). Bounded at the newest
 * 100 reports. */
import { useState } from "react";

import { Sparkline } from "../../../components/charts";
import { Card, DataTable } from "../../../components/ui";
import { formatTime, metricSeries, numericMetricKeys } from "../../../lib/domain";
import { useInstances, useRappPerformance } from "../data/queries";

/** The box. */
export function RappPerformance() {
  const instances = useInstances();
  const [id, setId] = useState("");
  const chosen = id || instances.data?.find((i) => i.state === "RUNNING")?.instanceId || "";
  const perf = useRappPerformance(chosen);
  const keys = numericMetricKeys(perf.data ?? []);
  return (
    <Card section="kpis.rapp" title="rApp performance reports" actions={
      <select value={chosen} onChange={(e) => setId(e.target.value)} aria-label="Instance">
        <option value="">Choose an instance…</option>
        {instances.data?.map((i) => <option key={i.instanceId} value={i.instanceId}>{i.instanceId.slice(0, 8)} ({i.state})</option>)}
      </select>}>
      {!chosen ? <p className="muted">No instance selected.</p> : <>
        {keys.length === 0 ? <p className="muted">This instance hasn't reported performance yet (POST /instances/&#123;id&#125;/performance).</p>
          : <div className="spark-list">{keys.map((k) => <Sparkline key={k} points={metricSeries(perf.data!, k)} label={k} width={320} height={60} />)}</div>}
        <DataTable rows={perf.data} rowKey={(r) => r.reportId} loading={perf.isLoading} error={perf.error} empty="—" columns={[
          { header: "Reported", render: (r) => formatTime(r.reportedAt) },
          { header: "Metrics", render: (r) => <code className="small">{JSON.stringify(r.metrics)}</code> },
        ]} />
      </>}
    </Card>
  );
}
