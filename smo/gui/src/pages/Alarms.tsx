import { useMemo, useState } from "react";

import { POLL, useSmo } from "../api/hooks";
import type { Alarm, OCloudAlarm } from "../api/types";
import { ActionButton, Can, Card, DataTable, Drawer, Field, Id, KeyValue, PageHeader, SeverityChip, StateBadge, Tabs, useHashTab } from "../components/ui";
import { countBySeverity, formatTime, SEVERITIES, sortAlarms } from "../lib/domain";

const TABS = ["ran", "ocloud"] as const;

export function Alarms() {
  const [tab, setTab] = useHashTab(TABS, "ran");
  return (
    <>
      <PageHeader title="Alarms" subtitle="Refreshes every 5 s. Acknowledge / clear are recorded against your GUI user." />
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "ran", label: "RAN NF alarms (O1)" }, { id: "ocloud", label: "O-Cloud alarms (FOCOM)" }]} />
      {tab === "ran" ? <RanAlarms /> : <OCloudAlarms />}
    </>
  );
}

function RanAlarms() {
  const [me, setMe] = useState("");
  const [severity, setSeverity] = useState("");
  const [ack, setAck] = useState("");
  const [showCleared, setShowCleared] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const alarms = useSmo<Alarm[]>("/ran-nf-oam/alarms", { managed_element_ref: me, severity }, { refetchInterval: POLL.alarms });
  const all = useSmo<Alarm[]>("/ran-nf-oam/alarms", undefined, { refetchInterval: POLL.alarms });
  const mes = useMemo(() => [...new Set((all.data ?? []).map((a) => a.managedElementRef))].sort(), [all.data]);
  const rows = useMemo(() => sortAlarms((alarms.data ?? []).filter((a) =>
    (showCleared || severity === "cleared" || a.severity !== "cleared") && (!ack || a.ackState === ack))), [alarms.data, showCleared, severity, ack]);
  const counts = countBySeverity((all.data ?? []).filter((a) => a.severity !== "cleared"));
  const current = all.data?.find((a) => a.alarmId === selected);
  return (
    <>
      <div className="sev-counts filters">
        {SEVERITIES.map((s) => (
          <button key={s} className={`sev-count sev-${s} ${severity === s ? "active" : ""}`} onClick={() => setSeverity(severity === s ? "" : s)}>
            <span>{counts[s]}</span>{s}
          </button>
        ))}
      </div>
      <Card title="Alarm list" actions={<>
        <select value={me} onChange={(e) => setMe(e.target.value)} aria-label="Managed element"><option value="">All managed elements</option>{mes.map((m) => <option key={m}>{m}</option>)}</select>
        <select value={severity} onChange={(e) => setSeverity(e.target.value)} aria-label="Severity"><option value="">All severities</option>{[...SEVERITIES, "cleared"].map((s) => <option key={s}>{s}</option>)}</select>
        <select value={ack} onChange={(e) => setAck(e.target.value)} aria-label="Ack state"><option value="">Any ack state</option><option>UNACKNOWLEDGED</option><option>ACKNOWLEDGED</option></select>
        <label className="check"><input type="checkbox" checked={showCleared} onChange={(e) => setShowCleared(e.target.checked)} /> show cleared</label>
      </>}>
        <DataTable rows={rows} loading={alarms.isLoading} error={alarms.error} rowKey={(a) => a.alarmId} empty="No alarms match."
          onRowClick={(a) => setSelected(a.alarmId)} selectedKey={selected} columns={[
            { header: "Severity", render: (a) => <SeverityChip severity={a.severity} /> },
            { header: "Managed element", render: (a) => <><strong>{a.managedElementRef}</strong><div className="muted small">{a.sourceAlarmId}</div></> },
            { header: "Probable cause", render: (a) => a.probableCause ?? <span className="muted">—</span> },
            { header: "Specific problem", render: (a) => a.specificProblem ?? <span className="muted">—</span> },
            { header: "Type", render: (a) => a.alarmType ?? <span className="muted">—</span> },
            { header: "Raised", render: (a) => formatTime(a.raisedAt) },
            { header: "Ack", render: (a) => <StateBadge state={a.ackState} /> },
            { header: "", className: "actions", render: (a) => <AlarmActions alarm={a} /> },
          ]} />
      </Card>
      <Can method="POST" path="/ran-nf-oam/alarms/ingest"><InjectAlarm /></Can>
      {current && <AlarmDrawer alarm={current} onClose={() => setSelected(null)} />}
    </>
  );
}

function AlarmActions({ alarm }: { alarm: Alarm }) {
  const base = `/ran-nf-oam/alarms/${alarm.alarmId}`;
  const cleared = alarm.severity === "cleared";
  return (
    <div className="row gap end">
      {alarm.ackState === "UNACKNOWLEDGED"
        ? <ActionButton label="Ack" action={{ method: "PATCH", path: `${base}/ack`, query: { new_state: "ACKNOWLEDGED" }, success: "Alarm acknowledged" }} />
        : <ActionButton label="Unack" action={{ method: "PATCH", path: `${base}/ack`, query: { new_state: "UNACKNOWLEDGED" }, success: "Alarm unacknowledged" }} />}
      {!cleared && <ActionButton label="Clear" tone="primary" action={{ method: "PATCH", path: `${base}/clear`, success: "Alarm cleared" }} />}
    </div>
  );
}

function AlarmDrawer({ alarm, onClose }: { alarm: Alarm; onClose: () => void }) {
  return (
    <Drawer title={<>Alarm <Id value={alarm.alarmId} /></>} onClose={onClose}>
      <div className="row between"><SeverityChip severity={alarm.severity} /><AlarmActions alarm={alarm} /></div>
      <h3>3GPP TS 28.532 / 28.111 fault fields</h3>
      <KeyValue items={[
        ["alarmId", <code>{alarm.alarmId}</code>], ["Source alarm ID (ME-native)", alarm.sourceAlarmId],
        ["Managed element", alarm.managedElementRef], ["perceivedSeverity", alarm.severity], ["alarmType", alarm.alarmType],
        ["probableCause", alarm.probableCause], ["specificProblem", alarm.specificProblem],
        ["rootCauseIndicator", alarm.rootCauseIndicator ? "yes" : "no"], ["proposedRepairActions", alarm.proposedRepairActions],
        ["correlationGroup", alarm.correlationGroup],
        ["correlatedNotifications", alarm.correlatedNotifications.length ? alarm.correlatedNotifications.map((c) => <div key={c}><code>{c}</code></div>) : null],
      ]} />
      <h3>Lifecycle</h3>
      <KeyValue items={[
        ["Raised", formatTime(alarm.raisedAt)], ["ackState", alarm.ackState], ["ackUserId", alarm.ackUserId],
        ["alarmChangedTime", formatTime(alarm.changedAt)], ["alarmClearedTime", formatTime(alarm.clearedAt)], ["clearUserId", alarm.clearUserId],
      ]} />
    </Drawer>
  );
}

function InjectAlarm() {
  const [f, setF] = useState({ source_alarm_id: "", managed_element_ref: "", severity: "major", probable_cause: "", specific_problem: "", alarm_type: "COMMUNICATIONS_ALARM" });
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  return (
    <details className="admin-tools card">
      <summary>Admin: inject a test alarm (what an ME's NotifyNewAlarm would carry)</summary>
      <p className="muted small">The managed element must already be registered (Infrastructure → O1 endpoints).</p>
      <div className="form grid cols-3 tight">
        <Field label="Managed element"><input value={f.managed_element_ref} onChange={set("managed_element_ref")} placeholder="ME-1" /></Field>
        <Field label="Source alarm ID"><input value={f.source_alarm_id} onChange={set("source_alarm_id")} placeholder="odu-17" /></Field>
        <Field label="Severity"><select value={f.severity} onChange={set("severity")}>{SEVERITIES.map((s) => <option key={s}>{s}</option>)}</select></Field>
        <Field label="Probable cause"><input value={f.probable_cause} onChange={set("probable_cause")} placeholder="LOSS_OF_SIGNAL" /></Field>
        <Field label="Specific problem"><input value={f.specific_problem} onChange={set("specific_problem")} /></Field>
        <Field label="Alarm type"><select value={f.alarm_type} onChange={set("alarm_type")}>{["COMMUNICATIONS_ALARM", "QUALITY_OF_SERVICE_ALARM", "PROCESSING_ERROR_ALARM", "EQUIPMENT_ALARM", "ENVIRONMENTAL_ALARM"].map((t) => <option key={t}>{t}</option>)}</select></Field>
      </div>
      <ActionButton label="Inject alarm" disabled={!f.managed_element_ref || !f.source_alarm_id} action={{ method: "POST", path: "/ran-nf-oam/alarms/ingest", query: f, success: "Alarm ingested" }} />
    </details>
  );
}

function OCloudAlarms() {
  const alarms = useSmo<OCloudAlarm[]>("/focom/alarms", undefined, { refetchInterval: POLL.alarms });
  return (
    <Card title="O-Cloud infrastructure alarms" actions={<span className="muted small">FOCOM (O2ims) — read-only</span>}>
      <DataTable rows={alarms.data ? sortAlarms(alarms.data.map((a) => ({ ...a, raisedAt: null }))) : undefined} loading={alarms.isLoading} error={alarms.error}
        rowKey={(a) => a.alarmId} empty="No O-Cloud alarms." columns={[
          { header: "Severity", render: (a) => <SeverityChip severity={a.severity} /> },
          { header: "Resource", render: (a) => <code>{a.resourceRef}</code> },
          { header: "Alarm", render: (a) => <Id value={a.alarmId} /> },
        ]} />
    </Card>
  );
}
