/** Sections `kpis.definitions` and `kpis.schedules` (Definitions tab): the KPI definitions of RAN NF OAM (formula over PM counters, unit,
 * counters) with "Add the standard set", Define, Edit and Delete; and the schedules that publish them to DME on a timer, with Add, Edit and
 * Delete. Every write is admin-only in the BFF table and gated here with `Can` / `ActionButton`. Both lists are bounded at 200 (a KPI catalogue
 * is small; the schedule route is not paged). */
import { useState } from "react";

import type { KpiDef, KpiScheduleRow } from "../../../api/types";
import { ActionButton, Can, Card, DataTable, StateBadge } from "../../../components/ui";
import { describeSeconds, formatTime } from "../../../lib/domain";
import { KPI_DEFINITIONS, KPI_SCHEDULES, useKpiDefinitions, useKpiSchedules } from "../data/queries";
import { DefineKpi, EditSchedule } from "./KpiDialogs";

/** The KPI definitions, and the schedules that publish them to DME. */
export function KpiDefinitions() {
  const defs = useKpiDefinitions();
  const schedules = useKpiSchedules();
  const [editing, setEditing] = useState<KpiDef | "new" | null>(null);
  const [scheduling, setScheduling] = useState<KpiScheduleRow | "new" | null>(null);
  return (
    <>
      <Card section="kpis.definitions" title="KPI definitions" actions={<>
        <ActionButton label="Add the standard set" title="Defines the six standard KPIs that are not defined yet; an edited one is kept"
          action={{ method: "POST", path: `${KPI_DEFINITIONS}/standard`, success: "Standard KPIs added" }} />
        <Can method="PUT" path={`${KPI_DEFINITIONS}/x`}><button type="button" className="btn primary" onClick={() => setEditing("new")}>Define KPI</button></Can>
      </>}>
        <p className="muted small">A KPI is a formula over PM counters. The standard set is over the counters this build carries, not the TS 28.554 definitions.</p>
        <DataTable rows={defs.data} rowKey={(k) => k.name} error={defs.error} loading={defs.isLoading} empty="No KPI is defined yet." columns={[
          { header: "KPI", render: (k) => <strong>{k.name}</strong> },
          { header: "Formula", render: (k) => <code className="small">{k.formula}</code> },
          { header: "Unit", render: (k) => k.unit ?? <span className="muted">—</span> },
          { header: "Counters", render: (k) => k.counters.map((c) => c.counter).join(", ") || <span className="muted">—</span> },
          { header: "", className: "actions", render: (k) => (
            <div className="row gap end">
              <Can method="PUT" path={`${KPI_DEFINITIONS}/${k.name}`}><button type="button" className="btn" onClick={() => setEditing(k)}>Edit</button></Can>
              <ActionButton label="Delete" tone="danger" confirm={`Delete the KPI ${k.name}? A schedule or guard that names it will report an error.`}
                action={{ method: "DELETE", path: `${KPI_DEFINITIONS}/${k.name}`, success: "KPI deleted" }} />
            </div>
          ) },
        ]} />
      </Card>

      <Card section="kpis.schedules" title="KPI schedules" actions={<Can method="PUT" path={`${KPI_SCHEDULES}/x`}><button type="button" className="btn primary" onClick={() => setScheduling("new")}>Add schedule</button></Can>}>
        <p className="muted small">A schedule publishes a KPI to DME on a timer, as the type <code>RAN.KPI.&lt;name&gt;</code>, so an rApp reads it like any other data. It runs only while the RAN NF OAM worker is running.</p>
        <DataTable rows={schedules.data} rowKey={(s) => s.scheduleId} error={schedules.error} loading={schedules.isLoading} empty="No KPI is published on a timer." columns={[
          { header: "Schedule", render: (s) => <strong>{s.scheduleId}</strong> },
          { header: "KPI", render: (s) => s.kpi },
          { header: "Publishes", render: (s) => <>every {describeSeconds(s.intervalSeconds)}, over the last {describeSeconds(s.lookbackSeconds)}, per {s.groupBy}</> },
          { header: "State", render: (s) => <StateBadge state={s.enabled ? "ENABLED" : "DISABLED"} /> },
          { header: "Last run", render: (s) => s.lastRunAt
            ? <span title={s.lastDetail ?? undefined}><StateBadge state={s.lastStatus === "OK" ? "COMPLETED" : "FAILED"} /> {formatTime(s.lastRunAt)}</span> : <span className="muted">not yet</span> },
          { header: "Next", render: (s) => formatTime(s.nextRunAt) },
          { header: "", className: "actions", render: (s) => (
            <div className="row gap end">
              <Can method="PUT" path={`${KPI_SCHEDULES}/${s.scheduleId}`}><button type="button" className="btn" onClick={() => setScheduling(s)}>Edit</button></Can>
              <ActionButton label="Delete" tone="danger" confirm={`Stop publishing with ${s.scheduleId}?`}
                action={{ method: "DELETE", path: `${KPI_SCHEDULES}/${s.scheduleId}`, success: "Schedule deleted" }} />
            </div>
          ) },
        ]} />
      </Card>
      {editing && <DefineKpi current={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
      {scheduling && <EditSchedule current={scheduling === "new" ? null : scheduling} kpis={defs.data ?? []} onClose={() => setScheduling(null)} />}
    </>
  );
}
