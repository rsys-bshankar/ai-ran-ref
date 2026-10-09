import { useState } from "react";

import { useSmo, useSmoAction } from "../api/hooks";
import { useAuth } from "../auth/AuthContext";
import type { ElementOnboarding, O1Endpoint, OnboardingStatus, OnboardingTemplate } from "../api/types";
import { ConfigJobDrawer } from "../components/ConfigJobDrawer";
import { ActionButton, Can, Card, DataTable, Drawer, Field, Id, KeyValue, Modal, StateBadge } from "../components/ui";
import { formatTime } from "../lib/domain";
import { blankTemplate, ONBOARDING_MEANING, onboardingActions, templateForm, templatePayload, type TemplateForm } from "../lib/lifecycle";
import { LifecycleWatchers } from "./LifecycleWatchers";

const TEMPLATES = "/ran-nf-oam/onboarding-templates";
const ONBOARDING = "/ran-nf-oam/element-onboarding";
const STATUSES: OnboardingStatus[] = ["DISCOVERED", "NO_TEMPLATE", "TEMPLATE_SELECTED", "APPLYING", "ONBOARDED", "FAILED"];

/** Zero-touch onboarding (PR-MGT-14, GUI step 14.6): the templates that say how a new element of a type is configured, and where each element is in its onboarding.
 * Reads are open to a viewer; a template is changed by an admin; selecting and applying one is an operator's action, recorded under the signed-in user. */
export function OnboardingTab() {
  return (
    <>
      <Templates />
      <Elements />
      <LifecycleWatchers />
    </>
  );
}

// ---------------------------------------------------------------- templates

function Templates() {
  const templates = useSmo<OnboardingTemplate[]>(TEMPLATES, { limit: 200 });
  const [editing, setEditing] = useState<OnboardingTemplate | "new" | null>(null);
  return (
    <Card title="Onboarding templates" actions={<Can method="PUT" path={`${TEMPLATES}/new`}><button className="btn primary" onClick={() => setEditing("new")}>New template…</button></Can>}>
      <p className="muted small">
        A template is the initial configuration of an element type. When an element registers, the enabled template for its type (one that names its vendor before a general one) is
        selected for it. Defining a template changes no element that registered earlier: select it for them below. Nothing is written until a template is applied.
      </p>
      <DataTable rows={templates.data} rowKey={(t) => t.name} error={templates.error} loading={templates.isLoading} empty="No template is defined: elements register as they always did."
        onRowClick={(t) => setEditing(t)} columns={[
          { header: "Template", render: (t) => <strong>{t.name}</strong> },
          { header: "Applies to", render: (t) => <>{t.entityType}{t.vendorName ? <span className="muted"> · {t.vendorName}</span> : <span className="muted"> · any vendor</span>}</> },
          { header: "Changes", render: (t) => t.changes.length },
          { header: "Software baseline", render: (t) => (t.softwareBaseline ? <>{t.softwareBaseline}{t.requireBaseline && <span className="muted small"> (required)</span>}</> : <span className="muted">—</span>) },
          { header: "Applied", render: (t) => (t.autoApply ? "on first heartbeat" : "by an operator") },
          { header: "State", render: (t) => <StateBadge state={t.enabled ? "ENABLED" : "DISABLED"} /> },
          { header: "", className: "actions", render: (t) => (
            <div className="row gap end">
              <Can method="PUT" path={`${TEMPLATES}/${t.name}`}><button className="btn" onClick={(e) => { e.stopPropagation(); setEditing(t); }}>Edit</button></Can>
              <ActionButton label="Delete" tone="danger" confirm={`Delete the template ${t.name}? Elements already matched to it keep their row; applying it to them fails until they are selected again.`}
                action={{ method: "DELETE", path: `${TEMPLATES}/${t.name}`, success: "Template deleted" }} />
            </div>
          ) },
        ]} />
      {editing && <TemplateEditor template={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
    </Card>
  );
}

function TemplateEditor({ template, onClose }: { template: OnboardingTemplate | null; onClose: () => void }) {
  const { can } = useAuth();
  const [form, setForm] = useState<TemplateForm>(template ? templateForm(template) : blankTemplate());
  const [problem, setProblem] = useState<string | null>(null);
  const action = useSmoAction();
  const set = <K extends keyof TemplateForm>(key: K, value: TemplateForm[K]) => setForm((f) => ({ ...f, [key]: value }));
  if (template && !can("PUT", `${TEMPLATES}/${template.name}`)) return <Modal title={`Template ${template.name}`} onClose={onClose}><TemplateDetails template={template} /></Modal>;
  const save = () => {
    const parsed = templatePayload(form, template !== null);
    if (!parsed.ok) return setProblem(parsed.error);
    setProblem(null);
    action.mutate({ method: "PUT", path: `${TEMPLATES}/${template ? template.name : parsed.name}`, json: parsed.body, success: template ? "Template saved" : "Template defined" }, { onSuccess: onClose });
  };
  return (
    <Modal title={template ? `Template ${template.name}` : "New onboarding template"} onClose={onClose}>
      <div className="form">
        <div className="grid cols-3 tight">
          <Field label="Name" hint={template ? "The name is the key; to rename, define a new one" : "Letters, digits, . _ -"}>
            <input value={form.name} onChange={(e) => set("name", e.target.value)} readOnly={template !== null} autoFocus={template === null} />
          </Field>
          <Field label="Entity type" hint="For example O-DU"><input value={form.entityType} onChange={(e) => set("entityType", e.target.value)} /></Field>
          <Field label="Vendor" hint="Blank: any vendor"><input value={form.vendorName} onChange={(e) => set("vendorName", e.target.value)} /></Field>
        </div>
        <Field label="Description"><input value={form.description} maxLength={500} onChange={(e) => set("description", e.target.value)} /></Field>
        <Field label="Software baseline" hint="The software version a new element is expected to run; a different one is flagged"><input value={form.softwareBaseline} onChange={(e) => set("softwareBaseline", e.target.value)} /></Field>
        <label className="row gap small"><input type="checkbox" checked={form.requireBaseline} onChange={(e) => set("requireBaseline", e.target.checked)} /> Stop the onboarding when the element does not run the baseline (otherwise it only flags it)</label>
        <label className="row gap small"><input type="checkbox" checked={form.autoApply} onChange={(e) => set("autoApply", e.target.checked)} /> Apply by itself when the element first reports in (otherwise an operator applies it)</label>
        <label className="row gap small"><input type="checkbox" checked={form.enabled} onChange={(e) => set("enabled", e.target.checked)} /> Enabled (a disabled template is never selected)</label>
        <Field label="Changes (JSON list)" hint="Each: managedFunctionRef (optional), attributeChanges, operation (merge, replace, create, delete, remove). The element is the new element's own, so no managedElementRef.">
          <textarea rows={9} value={form.changes} onChange={(e) => set("changes", e.target.value)} spellCheck={false} />
        </Field>
        {problem && <div className="error-box" role="alert">{problem}</div>}
        <div className="row gap end">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button className="btn primary" disabled={action.isPending} onClick={save}>{action.isPending ? "…" : "Save"}</button>
        </div>
      </div>
    </Modal>
  );
}

/** What a viewer sees of a template: every field, nothing to change. */
function TemplateDetails({ template }: { template: OnboardingTemplate }) {
  return (
    <KeyValue items={[
      ["Applies to", `${template.entityType}${template.vendorName ? ` · ${template.vendorName}` : " · any vendor"}`], ["Description", template.description],
      ["Software baseline", template.softwareBaseline ? `${template.softwareBaseline}${template.requireBaseline ? " (required)" : ""}` : null],
      ["Applied", template.autoApply ? "on the first heartbeat" : "by an operator"], ["State", template.enabled ? "enabled" : "disabled"],
      ["Created", formatTime(template.createdAt)], ["Last changed", formatTime(template.updatedAt)],
      ["Changes", <ul key="c" aria-label="Template changes">{template.changes.map((c, i) => (
        <li key={i} className="small"><code>{c.operation} {c.managedFunctionRef ?? "(the element)"} {JSON.stringify(c.attributeChanges)}</code></li>))}</ul>],
    ]} />
  );
}

// ---------------------------------------------------------------- the elements

function Elements() {
  const [status, setStatus] = useState<OnboardingStatus | "">("");
  const rows = useSmo<ElementOnboarding[]>(ONBOARDING, { status: status || undefined, limit: 200 }, { refetchInterval: 5_000 });
  const [applying, setApplying] = useState<ElementOnboarding | null>(null);
  const [selecting, setSelecting] = useState<string | "new" | null>(null);
  const [open, setOpen] = useState<ElementOnboarding | null>(null);
  return (
    <Card title="Onboarding of elements" actions={<>
      <select value={status} onChange={(e) => setStatus(e.target.value as OnboardingStatus | "")} aria-label="Onboarding status"><option value="">All states</option>{STATUSES.map((s) => <option key={s}>{s}</option>)}</select>
      <Can method="POST" path={`${ONBOARDING}/any/select`}><button className="btn" onClick={() => setSelecting("new")}>Select a template for an element…</button></Can>
    </>}>
      <p className="muted small">
        One row for each element that registered while a template existed, or that an operator selected a template for. A flagged software version does not stop an onboarding unless the template requires the baseline.
      </p>
      <DataTable rows={rows.data} rowKey={(r) => r.managedElementRef} error={rows.error} loading={rows.isLoading} empty="No element has been matched against a template." onRowClick={setOpen} columns={[
        { header: "Element", render: (r) => <strong>{r.managedElementRef}</strong> },
        { header: "Onboarding", render: (r) => <span title={ONBOARDING_MEANING[r.status]}><StateBadge state={r.status} /></span> },
        { header: "Template", render: (r) => r.templateName ?? <span className="muted">—</span> },
        { header: "Software", render: (r) => <>{r.softwareVersion ?? <span className="muted">not reported</span>}{r.softwareBaseline && <span className="muted small"> (baseline {r.softwareBaseline})</span>} <StateBadge state={r.softwareCheck} /></> },
        { header: "Detail", render: (r) => (r.detail ? <span className="small" title={r.detail}>{r.detail.length > 70 ? `${r.detail.slice(0, 70)}…` : r.detail}</span> : <span className="muted">—</span>) },
        { header: "Changed", render: (r) => formatTime(r.updatedAt) },
        { header: "", className: "actions", render: (r) => <RowActions row={r} onApply={() => setApplying(r)} onSelect={() => setSelecting(r.managedElementRef)} /> },
      ]} />
      {applying && <ApplyModal row={applying} onClose={() => setApplying(null)} />}
      {selecting && <SelectModal element={selecting === "new" ? null : selecting} onClose={() => setSelecting(null)} />}
      {open && <OnboardingDrawer row={open} onClose={() => setOpen(null)} />}
    </Card>
  );
}

function RowActions({ row, onApply, onSelect }: { row: ElementOnboarding; onApply: () => void; onSelect: () => void }) {
  const allowed = onboardingActions(row.status);
  return (
    <div className="row gap end">
      {allowed.apply && <Can method="POST" path={`${ONBOARDING}/${row.managedElementRef}/apply`}><button className="btn primary" onClick={(e) => { e.stopPropagation(); onApply(); }}>{row.status === "TEMPLATE_SELECTED" ? "Apply" : "Apply again"}</button></Can>}
      {allowed.select && <Can method="POST" path={`${ONBOARDING}/${row.managedElementRef}/select`}><button className="btn" onClick={(e) => { e.stopPropagation(); onSelect(); }}>Select…</button></Can>}
    </div>
  );
}

function ApplyModal({ row, onClose }: { row: ElementOnboarding; onClose: () => void }) {
  const [version, setVersion] = useState(row.softwareVersion ?? "");
  const action = useSmoAction();
  return (
    <Modal title={`Apply ${row.templateName ?? "the template"} to ${row.managedElementRef}`} onClose={onClose}>
      <p className="muted small">
        This writes the template's changes to the element as a config job (MSAC, the schema check and the rollback of a failed write all apply). It is recorded under your name.
      </p>
      <Field label="Software version the element runs" hint="Optional; checked against the template's baseline"><input value={version} maxLength={100} onChange={(e) => setVersion(e.target.value)} autoFocus /></Field>
      <div className="row gap">
        <button className="btn primary" disabled={action.isPending} onClick={() => action.mutate(
          { method: "POST", path: `${ONBOARDING}/${row.managedElementRef}/apply`, json: version.trim() ? { softwareVersion: version.trim() } : {}, success: `Applying to ${row.managedElementRef}` }, { onSuccess: onClose })}>
          {action.isPending ? "…" : "Apply"}
        </button>
        <button className="btn ghost" onClick={onClose}>Cancel</button>
      </div>
    </Modal>
  );
}

/** Select (again) the template of an element: the best match, or one named. For an element with no row yet (registered before any template existed) pass `element` null and choose it. */
function SelectModal({ element, onClose }: { element: string | null; onClose: () => void }) {
  const templates = useSmo<OnboardingTemplate[]>(TEMPLATES, { limit: 200 });
  const endpoints = useSmo<O1Endpoint[]>("/ran-nf-oam/o1-adaptor-endpoints", { limit: 200 }, { enabled: element === null });
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
      <Field label="Software version the element runs" hint="Optional"><input value={version} maxLength={100} onChange={(e) => setVersion(e.target.value)} /></Field>
      <div className="row gap">
        <button className="btn primary" disabled={action.isPending || !chosen} onClick={() => action.mutate(
          { method: "POST", path: `${ONBOARDING}/${chosen}/select`, json: body, success: `Template selected for ${chosen}` }, { onSuccess: onClose })}>
          {action.isPending ? "…" : "Select"}
        </button>
        <button className="btn ghost" onClick={onClose}>Cancel</button>
      </div>
    </Modal>
  );
}

function OnboardingDrawer({ row, onClose }: { row: ElementOnboarding; onClose: () => void }) {
  const live = useSmo<ElementOnboarding>(`${ONBOARDING}/${row.managedElementRef}`, undefined, { refetchInterval: 5_000 });
  const data = live.data ?? row;
  const [job, setJob] = useState<string | null>(null);
  return (
    <Drawer title={<>Onboarding of {data.managedElementRef}</>} onClose={onClose}>
      <div className="row gap wrap"><StateBadge state={data.status} /><span className="muted small">{ONBOARDING_MEANING[data.status]}</span></div>
      <KeyValue items={[
        ["Template", data.templateName], ["Software version", data.softwareVersion], ["Baseline", data.softwareBaseline], ["Baseline check", <StateBadge key="c" state={data.softwareCheck} />],
        ["Config job", data.configJobId ? <button key="j" className="btn ghost" onClick={() => setJob(data.configJobId)}><Id value={data.configJobId} /></button> : null],
        ["Detail", data.detail], ["Registered", formatTime(data.createdAt)], ["Changed", formatTime(data.updatedAt)],
      ]} />
      {job && <ConfigJobDrawer id={job} onClose={() => setJob(null)} />}
    </Drawer>
  );
}
