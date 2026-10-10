/**
 * The Alarms page (route /alarms): the RAN NF alarms of RAN NF OAM (O1; filter, acknowledge, clear, a drawer of the 3GPP fault fields, FM subscriptions, and an admin tool to inject a test alarm) and the read-only
 * O-Cloud alarms of FOCOM. Polls every 5 s (`POLL.alarms`). Every signed-in role may read; Ack, Unack and Clear need the operator role, the FM subscription form and Unsubscribe are drawn only when the permission table allows them, and the injection tool is admin only, all
 * as the BFF's permission table says. The severity filter and the counts are computed in the browser from the full list.
 */

import { useMemo, useState } from "react";

import { POLL, useSmo } from "../api/hooks";
import type { Alarm, FmSubscription, O1Endpoint, OCloudAlarm } from "../api/types";
import { ActionButton, Can, Card, DataTable, Drawer, Field, Id, KeyValue, PageHeader, SeverityChip, StateBadge, Tabs, useHashTab } from "../components/ui";
import { countBySeverity, formatTime, SEVERITIES, sortAlarms } from "../lib/domain";

const TABS = ["ran", "ocloud"] as const;

/** The page: header and the two tabs, RAN NF alarms and O-Cloud alarms (the tab is kept in the URL hash). */
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

/**
 * The RAN alarm tab: severity counters (click to filter), the filtered and sorted list, the injection tool (admin), the FM subscriptions, and the drawer of the selected alarm.
 * Two reads of the same path are kept on purpose: the filtered one feeds the table, the unfiltered one feeds the counters, the managed-element choices and the open drawer, so they do not change when a filter is set.
 * Cleared alarms are hidden unless asked for or the severity filter is "cleared".
 */
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
            { header: "Managed element", render: (a) => <><strong>{a.managedElementRef}</strong>{a.managedFunctionRef && <> · <code className="small">{a.managedFunctionRef}</code></>}<div className="muted small">{a.sourceAlarmId}</div></> },
            { header: "Probable cause", render: (a) => a.probableCause ?? <span className="muted">—</span> },
            { header: "Specific problem", render: (a) => a.specificProblem ?? <span className="muted">—</span> },
            { header: "Type", render: (a) => a.alarmType ?? <span className="muted">—</span> },
            { header: "Raised", render: (a) => formatTime(a.raisedAt) },
            { header: "Ack", render: (a) => <StateBadge state={a.ackState} /> },
            { header: "", className: "actions", render: (a) => <AlarmActions alarm={a} /> },
          ]} />
      </Card>
      <Can method="POST" path="/ran-nf-oam/alarms/ingest"><InjectAlarm /></Can>
      <FmSubscriptions />
      {current && <AlarmDrawer alarm={current} onClose={() => setSelected(null)} />}
    </>
  );
}

// ---------------------------------------------------------------- FM → DME (HISTORY.md OI-6.7)

/**
 * The FM subscriptions of RAN NF OAM (HISTORY.md OI-6.7): the form to subscribe a managed element for fault records (push, pull or stream), and the list with Unsubscribe. A subscription only gives DME-mediated
 * visibility of alarms; it never clears one.
 */
function FmSubscriptions() {
  const subs = useSmo<FmSubscription[]>("/ran-nf-oam/fm-subscriptions");
  const endpoints = useSmo<O1Endpoint[]>("/ran-nf-oam/o1-adaptor-endpoints");
  const [f, setF] = useState({ managed_element_ref: "", delivery_method: "push" });
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  return (
    <>
      <Can method="POST" path="/ran-nf-oam/fm-subscriptions">
        <Card title="New FM subscription">
          <p className="muted small">SubscribeFM registers RAN NF OAM as a DME producer for RAN.FaultRecords (mirrors SubscribePM) — gives an rApp/AI-ML model DME-mediated visibility into outstanding/historical alarms. It never clears an alarm; that stays the Ack/Clear actions above.</p>
          <div className="form inline">
            <Field label="Managed element"><select value={f.managed_element_ref} onChange={set("managed_element_ref")}><option value="">Choose…</option>{endpoints.data?.map((e) => <option key={e.endpointId}>{e.managedElementRef}</option>)}</select></Field>
            <Field label="Delivery"><select value={f.delivery_method} onChange={set("delivery_method")}><option value="pull">pull</option><option value="push">push</option><option value="stream">stream</option></select></Field>
            <ActionButton label="Subscribe" tone="primary" disabled={!f.managed_element_ref} action={{ method: "POST", path: "/ran-nf-oam/fm-subscriptions", query: f, success: "FM subscription created" }} />
          </div>
        </Card>
      </Can>
      <Card title="FM subscriptions">
        <DataTable rows={subs.data} loading={subs.isLoading} error={subs.error} rowKey={(s) => s.subscriptionId} empty="No FM subscriptions." columns={[
          { header: "Subscription", render: (s) => <Id value={s.subscriptionId} /> },
          { header: "Managed element", render: (s) => s.managedElementRef },
          { header: "Delivery", render: (s) => s.deliveryMethod },
          { header: "Southbound engine", render: (s) => s.southboundEngine },
          { header: "", className: "actions", render: (s) => <ActionButton label="Unsubscribe" action={{ method: "DELETE", path: `/ran-nf-oam/fm-subscriptions/${s.subscriptionId}`, success: "Unsubscribed" }} /> },
        ]} />
      </Card>
    </>
  );
}

/**
 * The row buttons of an alarm: Ack or Unack by its acknowledgement state, and Clear unless it is already cleared. Each is a PATCH of RAN NF OAM through the BFF and is drawn only when the role may make it.
 */
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

/**
 * The detail drawer of one alarm: its TS 28.532 / 28.111 fault fields (as the field names the standards use) and its lifecycle (raised, acknowledged, changed and cleared times and users).
 */
function AlarmDrawer({ alarm, onClose }: { alarm: Alarm; onClose: () => void }) {
  return (
    <Drawer title={<>Alarm <Id value={alarm.alarmId} /></>} onClose={onClose}>
      <div className="row between"><SeverityChip severity={alarm.severity} /><AlarmActions alarm={alarm} /></div>
      <h3>3GPP TS 28.532 / 28.111 fault fields</h3>
      <KeyValue items={[
        ["alarmId", <code>{alarm.alarmId}</code>], ["Source alarm ID (ME-native)", alarm.sourceAlarmId],
        ["Managed element", alarm.managedElementRef], ["Managed function", alarm.managedFunctionRef ?? "the whole element"], ["perceivedSeverity", alarm.severity], ["alarmType", alarm.alarmType],
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

/**
 * The admin tool that sends a test alarm as an element's NotifyNewAlarm would carry it (the element must already be registered). The button is enabled only with a managed element and a source alarm id;
 * an empty managed function means the whole element.
 */
function InjectAlarm() {
  const [f, setF] = useState({ source_alarm_id: "", managed_element_ref: "", managed_function_ref: "", severity: "major", probable_cause: "", specific_problem: "", alarm_type: "COMMUNICATIONS_ALARM" });
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  return (
    <details className="admin-tools card">
      <summary>Admin: inject a test alarm (what an ME's NotifyNewAlarm would carry)</summary>
      <p className="muted small">The managed element must already be registered (Infrastructure → O1 endpoints).</p>
      <div className="form grid cols-3 tight">
        <Field label="Managed element"><input value={f.managed_element_ref} onChange={set("managed_element_ref")} placeholder="ME-1" /></Field>
        <Field label="Source alarm ID"><input value={f.source_alarm_id} onChange={set("source_alarm_id")} placeholder="odu-17" /></Field>
        <Field label="Managed function" hint="the cell it is about, e.g. NRCellDU=101; empty = the whole element"><input value={f.managed_function_ref} onChange={set("managed_function_ref")} placeholder="NRCellDU=101" /></Field>
        <Field label="Severity"><select value={f.severity} onChange={set("severity")}>{SEVERITIES.map((s) => <option key={s}>{s}</option>)}</select></Field>
        <Field label="Probable cause"><input value={f.probable_cause} onChange={set("probable_cause")} placeholder="LOSS_OF_SIGNAL" /></Field>
        <Field label="Specific problem"><input value={f.specific_problem} onChange={set("specific_problem")} /></Field>
        <Field label="Alarm type"><select value={f.alarm_type} onChange={set("alarm_type")}>{["COMMUNICATIONS_ALARM", "QUALITY_OF_SERVICE_ALARM", "PROCESSING_ERROR_ALARM", "EQUIPMENT_ALARM", "ENVIRONMENTAL_ALARM"].map((t) => <option key={t}>{t}</option>)}</select></Field>
      </div>
      <ActionButton label="Inject alarm" disabled={!f.managed_element_ref || !f.source_alarm_id} action={{ method: "POST", path: "/ran-nf-oam/alarms/ingest", query: { ...f, managed_function_ref: f.managed_function_ref || undefined }, success: "Alarm ingested" }} />
    </details>
  );
}

/** The FOCOM (O2ims) infrastructure alarms: severity, resource and alarm id, read-only, sorted by severity. */
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
