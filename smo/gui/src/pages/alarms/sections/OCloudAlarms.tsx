/** Section `alarms.ocloud` (tab "O-Cloud"): O-Cloud infrastructure alarms from FOCOM (O2ims), read-only, in the shared server table (SCALE.md P1).
 * Filters are the route's own parameters: `severity` and `resource_ref`. */
import { useState } from "react";

import { POLL } from "../../../api/hooks";
import type { OCloudAlarm } from "../../../api/types";
import { Card, Id, SeverityChip } from "../../../components/ui";
import { ServerTable } from "../../../kit/ServerTable";
import { SEVERITIES } from "../../../lib/domain";
import { OCLOUD_ALARMS } from "../data/queries";

/** The O-Cloud alarm table. */
export function OCloudAlarms() {
  const [severity, setSeverity] = useState("");
  const [resource, setResource] = useState("");
  return (
    <Card section="alarms.ocloud" title="O-Cloud infrastructure alarms" sub="FOCOM (O2ims) — read-only" actions={<>
      <input value={resource} onChange={(e) => setResource(e.target.value)} placeholder="Resource" aria-label="Resource" />
      <select value={severity} onChange={(e) => setSeverity(e.target.value)} aria-label="O-Cloud severity">
        <option value="">All severities</option>{[...SEVERITIES, "cleared"].map((s) => <option key={s}>{s}</option>)}
      </select>
    </>}>
      <ServerTable<OCloudAlarm> path={OCLOUD_ALARMS} query={{ severity: severity || undefined, resource_ref: resource.trim() || undefined }}
        refetchInterval={POLL.alarms} rowKey={(a) => a.alarmId} empty="No O-Cloud alarms." columns={[
          { header: "Severity", render: (a) => <SeverityChip severity={a.severity} /> },
          { header: "Resource", render: (a) => <code>{a.resourceRef}</code> },
          { header: "Alarm", render: (a) => <Id value={a.alarmId} /> },
        ]} />
    </Card>
  );
}
