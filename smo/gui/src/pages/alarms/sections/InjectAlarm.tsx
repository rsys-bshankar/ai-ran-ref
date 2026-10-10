/** Section `alarms.inject`: the admin tool that injects a test alarm (what an ME's NotifyNewAlarm would carry) through
 * `POST /ran-nf-oam/alarms/ingest`. The page shows it only to a role that may make that call. */
import { useState } from "react";

import { ActionButton, Field } from "../../../components/ui";
import { SEVERITIES } from "../../../lib/domain";
import { RAN_ALARMS } from "../data/queries";

const ALARM_TYPES = ["COMMUNICATIONS_ALARM", "QUALITY_OF_SERVICE_ALARM", "PROCESSING_ERROR_ALARM", "EQUIPMENT_ALARM", "ENVIRONMENTAL_ALARM"];

/** The collapsible form. */
export function InjectAlarm() {
  const [f, setF] = useState({ source_alarm_id: "", managed_element_ref: "", managed_function_ref: "", severity: "major", probable_cause: "", specific_problem: "", alarm_type: "COMMUNICATIONS_ALARM" });
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  return (
    <details className="admin-tools card" data-section="alarms.inject">
      <summary>Admin: inject a test alarm (what an ME's NotifyNewAlarm would carry)</summary>
      <p className="muted small">The managed element must already be registered (Infrastructure → O1 endpoints).</p>
      <div className="form grid cols-3 tight">
        <Field label="Managed element"><input value={f.managed_element_ref} onChange={set("managed_element_ref")} placeholder="ME-1" /></Field>
        <Field label="Source alarm ID"><input value={f.source_alarm_id} onChange={set("source_alarm_id")} placeholder="odu-17" /></Field>
        <Field label="Managed function" hint="the cell it is about, e.g. NRCellDU=101; empty = the whole element"><input value={f.managed_function_ref} onChange={set("managed_function_ref")} placeholder="NRCellDU=101" /></Field>
        <Field label="Severity"><select value={f.severity} onChange={set("severity")}>{SEVERITIES.map((s) => <option key={s}>{s}</option>)}</select></Field>
        <Field label="Probable cause"><input value={f.probable_cause} onChange={set("probable_cause")} placeholder="LOSS_OF_SIGNAL" /></Field>
        <Field label="Specific problem"><input value={f.specific_problem} onChange={set("specific_problem")} /></Field>
        <Field label="Alarm type"><select value={f.alarm_type} onChange={set("alarm_type")}>{ALARM_TYPES.map((t) => <option key={t}>{t}</option>)}</select></Field>
      </div>
      <ActionButton label="Inject alarm" disabled={!f.managed_element_ref || !f.source_alarm_id} action={{ method: "POST", path: `${RAN_ALARMS}/ingest`, query: { ...f, managed_function_ref: f.managed_function_ref || undefined }, success: "Alarm ingested" }} />
    </details>
  );
}
