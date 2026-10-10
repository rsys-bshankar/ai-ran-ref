/** The dialogs of the element onboarding box (`configuration.onboarding`, MGT-14.6): Apply (write the selected template to the element as a
 * config job, with the software version it runs), Select (match the element against the templates again, naming one or taking the best match;
 * also for an element that has no onboarding row yet) and the detail drawer of one element's onboarding. Each posts through `useSmoAction` and
 * closes on success; who asked (`requestedBy`) is never sent: the GUI BFF sets it from the signed-in user. */
import { useState } from "react";

import { useSmoAction } from "../../../api/hooks";
import type { ElementOnboarding } from "../../../api/types";
import { ConfigJobDrawer } from "../../../components/ConfigJobDrawer";
import { Drawer, Field, Id, KeyValue, Modal, StateBadge } from "../../../components/ui";
import { formatTime } from "../../../lib/domain";
import { ONBOARDING_MEANING } from "../../../lib/lifecycle";
import { onboardingPath, useEndpointChoices, useOnboardingRow, useTemplates } from "../data/queries";

/**
 * Applies the row's template: asks for the software version the element runs (pre-filled with the one the row knows; blank sends an empty body), posts to the
 * element's /apply route and closes on success. The apply is a config job on the element (MSAC, the schema check and the rollback of a failed write all apply).
 */
export function ApplyModal({ row, onClose }: { row: ElementOnboarding; onClose: () => void }) {
  const [version, setVersion] = useState(row.softwareVersion ?? "");
  const action = useSmoAction();
  return (
    <Modal title={`Apply ${row.templateName ?? "the template"} to ${row.managedElementRef}`} onClose={onClose}>
      <p className="muted small">
        This writes the template's changes to the element as a config job (MSAC, the schema check and the rollback of a failed write all apply). It is recorded under your name.
      </p>
      <Field label="Software version the element runs" hint="Optional; checked against the template's baseline"><input className="mono" value={version} maxLength={100} onChange={(e) => setVersion(e.target.value)} autoFocus /></Field>
      <div className="row gap">
        <button type="button" className="btn primary" disabled={action.isPending} onClick={() => action.mutate(
          { method: "POST", path: onboardingPath(row.managedElementRef, "apply"), json: version.trim() ? { softwareVersion: version.trim() } : {}, success: `Applying to ${row.managedElementRef}` }, { onSuccess: onClose })}>
          {action.isPending ? "…" : "Apply"}
        </button>
        <button type="button" className="btn ghost" onClick={onClose}>Cancel</button>
      </div>
    </Modal>
  );
}

/**
 * Selects (again) the template of an element: the best match, or one named (only enabled templates are offered). For an element with no row yet (it registered
 * before any template existed) pass `element` null and choose it from the registered O1 endpoints; Select stays disabled until one is chosen. Nothing is written.
 */
export function SelectModal({ element, onClose }: { element: string | null; onClose: () => void }) {
  const templates = useTemplates();
  const endpoints = useEndpointChoices(element === null);
  const [chosen, setChosen] = useState(element ?? "");
  const [template, setTemplate] = useState("");
  const [version, setVersion] = useState("");
  const action = useSmoAction();
  const body: Record<string, string> = {};
  if (template) body.template = template;
  if (version.trim()) body.softwareVersion = version.trim();
  return (
    <Modal title={element ? `Select a template for ${element}` : "Select a template for an element"} onClose={onClose}>
      <p className="muted small">Matches the element against the templates again. Nothing is written; applying is a separate step.</p>
      {element === null && (
        <Field label="Managed element">
          <select value={chosen} onChange={(e) => setChosen(e.target.value)} autoFocus>
            <option value="">Choose…</option>
            {endpoints.data?.map((e) => <option key={e.endpointId} value={e.managedElementRef}>{e.managedElementRef}</option>)}
          </select>
        </Field>
      )}
      <Field label="Template" hint="Blank: the best match for the element's type and vendor (it must be for the same entity type)">
        <select value={template} onChange={(e) => setTemplate(e.target.value)}>
          <option value="">Best match</option>
          {templates.data?.filter((t) => t.enabled || t.name === template).map((t) => <option key={t.name} value={t.name}>{t.name} ({t.entityType})</option>)}
        </select>
      </Field>
      <Field label="Software version the element runs" hint="Optional"><input className="mono" value={version} maxLength={100} onChange={(e) => setVersion(e.target.value)} /></Field>
      <div className="row gap">
        <button type="button" className="btn primary" disabled={action.isPending || !chosen} onClick={() => action.mutate(
          { method: "POST", path: onboardingPath(chosen, "select"), json: body, success: `Template selected for ${chosen}` }, { onSuccess: onClose })}>
          {action.isPending ? "…" : "Select"}
        </button>
        <button type="button" className="btn ghost" onClick={onClose}>Cancel</button>
      </div>
    </Modal>
  );
}

/** The detail of one element's onboarding: its state with the meaning, template, software check, the full detail, and the config job of the last apply (a click opens
 * it in a second drawer). Re-reads the row every five seconds and shows `row` until that answers. */
export function OnboardingDrawer({ row, onClose }: { row: ElementOnboarding; onClose: () => void }) {
  const live = useOnboardingRow(row.managedElementRef);
  const data = live.data ?? row;
  const [job, setJob] = useState<string | null>(null);
  return (
    <Drawer title={<>Onboarding of {data.managedElementRef}</>} onClose={onClose}>
      <div className="row gap wrap"><StateBadge state={data.status} /><span className="muted small">{ONBOARDING_MEANING[data.status]}</span></div>
      <KeyValue items={[
        ["Template", data.templateName], ["Software version", data.softwareVersion], ["Baseline", data.softwareBaseline], ["Baseline check", <StateBadge key="c" state={data.softwareCheck} />],
        ["Config job", data.configJobId ? <button key="j" type="button" className="btn ghost" onClick={() => setJob(data.configJobId)}><Id value={data.configJobId} /></button> : null],
        ["Detail", data.detail], ["Registered", formatTime(data.createdAt)], ["Changed", formatTime(data.updatedAt)],
      ]} />
      {job && <ConfigJobDrawer id={job} onClose={() => setJob(null)} />}
    </Drawer>
  );
}
