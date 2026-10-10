/**
 * The KPI definitions and schedules of RAN NF OAM (the "KPI definitions" tab of the KPIs page, `Kpis.tsx`): the formulas over PM counters, the add-the-standard-set action, and the schedules that publish a KPI to DME on a timer.
 * Reading is open to every signed-in role; defining, editing and deleting KPIs and schedules are admin calls (the BFF's permission table), drawn through `Can` and `ActionButton`. The forms check their input with `lib/domain.ts`
 * (`parseCounters`, `kpiNameProblem`, `schedulePayload`) before sending; RAN NF OAM validates again. The "Can" probes use a placeholder id (`.../x`) because the permission table is per route pattern, not per object.
 */

import { useState } from "react";

import { useSmo, useSmoAction } from "../api/hooks";
import type { KpiDef, KpiScheduleRow } from "../api/types";
import { ActionButton, Can, Card, DataTable, Field, Modal, StateBadge } from "../components/ui";
import { describeSeconds, formatTime, kpiNameProblem, parseCounters, schedulePayload, type ScheduleForm } from "../lib/domain";

const GROUPS = ["cell", "element", "sectorGroup", "incidentZone", "all"] as const;

/** The KPI definitions of RAN NF OAM, and the schedules that publish them to DME. */
export function KpiDefinitions() {
  const defs = useSmo<KpiDef[]>("/ran-nf-oam/kpi-definitions", { limit: 200 });
  const schedules = useSmo<KpiScheduleRow[]>("/ran-nf-oam/kpi-schedules", { limit: 200 });
  const [editing, setEditing] = useState<KpiDef | "new" | null>(null);
  const [scheduling, setScheduling] = useState<KpiScheduleRow | "new" | null>(null);
  return (
    <>
      <Card title="KPI definitions" actions={<>
        <ActionButton label="Add the standard set" title="Defines the six standard KPIs that are not defined yet; an edited one is kept"
          action={{ method: "POST", path: "/ran-nf-oam/kpi-definitions/standard", success: "Standard KPIs added" }} />
        <Can method="PUT" path="/ran-nf-oam/kpi-definitions/x"><button className="btn primary" onClick={() => setEditing("new")}>Define KPI</button></Can>
      </>}>
        <p className="muted small">A KPI is a formula over PM counters. The standard set is over the counters this build carries, not the TS 28.554 definitions.</p>
        <DataTable rows={defs.data} rowKey={(k) => k.name} error={defs.error} loading={defs.isLoading} empty="No KPI is defined yet." columns={[
          { header: "KPI", render: (k) => <strong>{k.name}</strong> },
          { header: "Formula", render: (k) => <code className="small">{k.formula}</code> },
          { header: "Unit", render: (k) => k.unit ?? <span className="muted">—</span> },
          { header: "Counters", render: (k) => k.counters.map((c) => c.counter).join(", ") || <span className="muted">—</span> },
          { header: "", className: "actions", render: (k) => (
            <div className="row gap end">
              <Can method="PUT" path={`/ran-nf-oam/kpi-definitions/${k.name}`}><button className="btn" onClick={() => setEditing(k)}>Edit</button></Can>
              <ActionButton label="Delete" tone="danger" confirm={`Delete the KPI ${k.name}? A schedule or guard that names it will report an error.`}
                action={{ method: "DELETE", path: `/ran-nf-oam/kpi-definitions/${k.name}`, success: "KPI deleted" }} />
            </div>
          ) },
        ]} />
      </Card>

      <Card title="KPI schedules" actions={<Can method="PUT" path="/ran-nf-oam/kpi-schedules/x"><button className="btn primary" onClick={() => setScheduling("new")}>Add schedule</button></Can>}>
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
              <Can method="PUT" path={`/ran-nf-oam/kpi-schedules/${s.scheduleId}`}><button className="btn" onClick={() => setScheduling(s)}>Edit</button></Can>
              <ActionButton label="Delete" tone="danger" confirm={`Stop publishing with ${s.scheduleId}?`}
                action={{ method: "DELETE", path: `/ran-nf-oam/kpi-schedules/${s.scheduleId}`, success: "Schedule deleted" }} />
            </div>
          ) },
        ]} />
      </Card>
      {editing && <DefineKpi current={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
      {scheduling && <EditSchedule current={scheduling === "new" ? null : scheduling} kpis={defs.data ?? []} onClose={() => setScheduling(null)} />}
    </>
  );
}

/**
 * The define or edit dialog of one KPI (PUT /kpi-definitions/<name>): name (fixed when editing), formula, unit, description and the counters as JSON. Save stays disabled while the name, the counters JSON or the formula has a problem.
 */
function DefineKpi({ current, onClose }: { current: KpiDef | null; onClose: () => void }) {
  const [name, setName] = useState(current?.name ?? "");
  const [formula, setFormula] = useState(current?.formula ?? "");
  const [unit, setUnit] = useState(current?.unit ?? "");
  const [description, setDescription] = useState(current?.description ?? "");
  const [counters, setCounters] = useState(JSON.stringify(current?.counters ?? [], null, 2));
  const parsed = parseCounters(counters);
  const nameProblem = current ? null : kpiNameProblem(name);
  const action = useSmoAction();
  const problem = nameProblem ?? (!parsed.ok ? parsed.error : !formula.trim() ? "Write the formula" : null);
  return (
    <Modal title={current ? `Edit ${current.name}` : "Define a KPI"} onClose={onClose}>
      <form className="form" onSubmit={(e) => {
        e.preventDefault();
        if (problem || !parsed.ok) return;
        action.mutate({ method: "PUT", path: `/ran-nf-oam/kpi-definitions/${name}`, success: "KPI saved",
          json: { formula: formula.trim(), counters: parsed.value, unit: unit.trim() || null, description: description.trim() || null } }, { onSuccess: onClose });
      }}>
        <Field label="Name" hint={current ? undefined : (name && nameProblem) ? <span className="text-bad">{nameProblem}</span> : "Letters, digits, _ . -"}>
          <input value={name} onChange={(e) => setName(e.target.value)} readOnly={!!current} required />
        </Field>
        <Field label="Formula" hint="Arithmetic, comparisons, min, max, abs, sqrt, round, log10 and ifelse(condition, then, else) over the variables below">
          <input value={formula} onChange={(e) => setFormula(e.target.value)} placeholder="100 * ok / n" required />
        </Field>
        <Field label="Counters (JSON)" hint={parsed.ok ? 'Which PM counter feeds which variable, and how its samples combine (sum, avg, min, max, last or count), for example [{"counter": "RRU.PrbTotDl", "variable": "prb", "aggregation": "avg"}]' : <span className="text-bad">{parsed.error}</span>}>
          <textarea rows={6} value={counters} onChange={(e) => setCounters(e.target.value)} spellCheck={false} />
        </Field>
        <div className="grid cols-2 tight">
          <Field label="Unit"><input value={unit} onChange={(e) => setUnit(e.target.value)} placeholder="%" /></Field>
          <Field label="Description"><input value={description} onChange={(e) => setDescription(e.target.value)} /></Field>
        </div>
        {problem && name && <div className="error-box" role="alert">{problem}</div>}
        <div className="row gap end"><button type="button" className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={!!problem || action.isPending}>Save</button></div>
      </form>
    </Modal>
  );
}

/**
 * The add or edit dialog of one KPI schedule (PUT /kpi-schedules/<id>): the schedule name (fixed when editing), the KPI, grouping, interval, look-back, optional element and cell, and whether it is enabled. The body is built and checked by `schedulePayload`.
 */
function EditSchedule({ current, kpis, onClose }: { current: KpiScheduleRow | null; kpis: KpiDef[]; onClose: () => void }) {
  const [id, setId] = useState(current?.scheduleId ?? "");
  const [form, setForm] = useState<ScheduleForm>({
    kpi: current?.kpi ?? "", intervalSeconds: String(current?.intervalSeconds ?? 600), lookbackSeconds: current ? String(current.lookbackSeconds) : "",
    groupBy: current?.groupBy ?? "cell", managedElementRef: current?.managedElementRef ?? "", cellId: current?.cellId ?? "", enabled: current?.enabled ?? true,
  });
  const set = <K extends keyof ScheduleForm>(k: K, v: ScheduleForm[K]) => setForm({ ...form, [k]: v });
  const payload = schedulePayload(form);
  const action = useSmoAction();
  const idProblem = !current && !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$/.test(id) ? "Letters, digits, _ . - (64 at most)" : null;
  return (
    <Modal title={current ? `Edit ${current.scheduleId}` : "Add a KPI schedule"} onClose={onClose}>
      <form className="form" onSubmit={(e) => {
        e.preventDefault();
        if (!payload.ok || idProblem) return;
        action.mutate({ method: "PUT", path: `/ran-nf-oam/kpi-schedules/${id}`, json: payload.body, success: "Schedule saved" }, { onSuccess: onClose });
      }}>
        <Field label="Schedule name" hint={idProblem && id ? <span className="text-bad">{idProblem}</span> : undefined}>
          <input value={id} onChange={(e) => setId(e.target.value)} readOnly={!!current} required />
        </Field>
        <div className="grid cols-2 tight">
          <Field label="KPI" hint={kpis.length === 0 ? "Define a KPI first" : undefined}>
            <select value={form.kpi} onChange={(e) => set("kpi", e.target.value)} required><option value="">Choose…</option>{kpis.map((k) => <option key={k.name}>{k.name}</option>)}</select>
          </Field>
          <Field label="Group by"><select value={form.groupBy} onChange={(e) => set("groupBy", e.target.value)}>{GROUPS.map((g) => <option key={g}>{g}</option>)}</select></Field>
          <Field label="Every, seconds" hint="60 to 86400"><input inputMode="numeric" value={form.intervalSeconds} onChange={(e) => set("intervalSeconds", e.target.value)} /></Field>
          <Field label="Over the last, seconds" hint="Blank: the interval"><input inputMode="numeric" value={form.lookbackSeconds} onChange={(e) => set("lookbackSeconds", e.target.value)} /></Field>
          <Field label="Only managed element"><input value={form.managedElementRef} onChange={(e) => set("managedElementRef", e.target.value)} /></Field>
          <Field label="Only cell"><input value={form.cellId} onChange={(e) => set("cellId", e.target.value)} /></Field>
        </div>
        <label className="row gap small"><input type="checkbox" checked={form.enabled} onChange={(e) => set("enabled", e.target.checked)} /> Enabled</label>
        {!payload.ok && form.kpi && <div className="error-box" role="alert">{payload.error}</div>}
        <div className="row gap end"><button type="button" className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={!payload.ok || !!idProblem || action.isPending}>Save</button></div>
      </form>
    </Modal>
  );
}
