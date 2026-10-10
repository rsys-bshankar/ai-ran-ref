/** Section `kpis.ocloud` (O-Cloud tab): FOCOM O-Cloud performance metrics as a server-paged table, filtered by resource on the server
 * (`resource_ref`). */
import { useState } from "react";

import type { OCloudMetric } from "../../../api/types";
import { Card } from "../../../components/ui";
import { ServerTable } from "../../../kit/ServerTable";
import { OCLOUD_PERFORMANCE } from "../data/queries";

/** The tab. */
export function OCloudPerformance() {
  const [resource, setResource] = useState("");
  return (
    <Card section="kpis.ocloud" title="O-Cloud performance (FOCOM)" actions={<input value={resource} onChange={(e) => setResource(e.target.value)} placeholder="Filter by resource" aria-label="Filter by resource" />}>
      <ServerTable<OCloudMetric> path={OCLOUD_PERFORMANCE} query={{ resource_ref: resource.trim() || undefined }} rowKey={(m) => `${m.resourceRef}/${m.metricName}/${m.value}`} empty="No O-Cloud metrics recorded." columns={[
        { header: "Resource", render: (m) => <code>{m.resourceRef}</code> }, { header: "Metric", render: (m) => m.metricName }, { header: "Value", render: (m) => m.value },
      ]} />
    </Card>
  );
}
