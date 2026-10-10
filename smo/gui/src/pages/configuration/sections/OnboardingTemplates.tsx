/** Configuration · onboarding templates (`configuration.templates`, MGT-14.6): how a new element of a type (and optionally a vendor) is first
 * configured: its changes, the software baseline it is expected to run, whether the baseline is required, and whether the template applies
 * itself on the element's first heartbeat. Lists `GET /onboarding-templates`; an admin defines or replaces one (`PUT /{name}`) and deletes one
 * (`DELETE /{name}`, asked first); a viewer or operator opens a read-only view. The form is checked by `templatePayload` (lib/lifecycle.ts,
 * the backend's own limits) before anything is sent. Defining a template changes no element that registered earlier: that is a Select on
 * the element onboarding box. */
import { useState } from "react";

import { useSmoAction } from "../../../api/hooks";
import type { OnboardingTemplate } from "../../../api/types";
import { useAuth } from "../../../auth/AuthContext";
import { ActionButton, Can, Card, DataTable, Field, KeyValue, Modal, StateBadge } from "../../../components/ui";
import { formatTime } from "../../../lib/domain";
import { blankTemplate, templateForm, templatePayload, type TemplateForm } from "../../../lib/lifecycle";
import { TEMPLATES_PATH, templatePath, useTemplates } from "../data/queries";

/** The templates card: the table and, for an admin, New / Edit / Delete; a row click opens the editor (read-only for a role that may not change it). */
export function OnboardingTemplates() {
  const templates = useTemplates();
  const [editing, setEditing] = useState<OnboardingTemplate | "new" | null>(null);
  return (
    <Card section="configuration.templates" title="Onboarding templates" sub="the first configuration of a new element, by type and vendor"
      actions={<Can method="PUT" path={`${TEMPLATES_PATH}/new`}><button type="button" className="btn primary" onClick={() => setEditing("new")}>New template…</button></Can>}>
      <p className="muted small">
        A template is the initial configuration of an element type. When an element registers, the enabled template for its type (one that names its vendor before a general one) is
        selected for it. Defining a template changes no element that registered earlier: select it for them below. Nothing is written until a template is applied.
      </p>
      <DataTable rows={templates.data} rowKey={(t) => t.name} error={templates.error} loading={templates.isLoading} empty="No template is defined: elements register as they always did."
        onRowClick={(t) => setEditing(t)} columns={[
          { header: "Template", render: (t) => <strong className="mono">{t.name}</strong> },
          { header: "Applies to", render: (t) => <>{t.entityType}<span className="muted"> · {t.vendorName ?? "any vendor"}</span></> },
          { header: "Changes", render: (t) => t.changes.length },
          { header: "Software baseline", render: (t) => (t.softwareBaseline ? <span className="mono">{t.softwareBaseline}{t.requireBaseline && <span className="muted small"> (required)</span>}</span> : <span className="muted">—</span>) },
          { header: "Applied", render: (t) => (t.autoApply ? "on first heartbeat" : "by an operator") },
          { header: "State", render: (t) => <StateBadge state={t.enabled ? "ENABLED" : "DISABLED"} /> },
          { header: "", className: "actions", render: (t) => (
            <div className="row gap end">
              <Can method="PUT" path={templatePath(t.name)}><button type="button" className="btn" onClick={(e) => { e.stopPropagation(); setEditing(t); }}>Edit</button></Can>
              <ActionButton label="Delete" tone="danger" confirm={`Delete the template ${t.name}? Elements already matched to it keep their row; applying it to them fails until they are selected again.`}
                action={{ method: "DELETE", path: templatePath(t.name), success: "Template deleted" }} />
            </div>
          ) },
        ]} />
      {editing && <TemplateEditor template={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
    </Card>
  );
}

/**
 * The modal that makes (`template` null) or edits a template. A problem found by `templatePayload` is shown and no call is made. Saving PUTs to the template's
 * name (the key: read-only when editing) and closes on success. A role that may not PUT the template is shown `TemplateDetails` instead of the form.
 */
function TemplateEditor({ template, onClose }: { template: OnboardingTemplate | null; onClose: () => void }) {
  const { can } = useAuth();
  const [form, setForm] = useState<TemplateForm>(template ? templateForm(template) : blankTemplate());
  const [problem, setProblem] = useState<string | null>(null);
  const action = useSmoAction();
  const set = <K extends keyof TemplateForm>(key: K, value: TemplateForm[K]) => setForm((f) => ({ ...f, [key]: value }));
  if (template && !can("PUT", templatePath(template.name))) return <Modal title={`Template ${template.name}`} onClose={onClose}><TemplateDetails template={template} /></Modal>;
  const save = () => {
    const parsed = templatePayload(form, template !== null);
    if (!parsed.ok) return setProblem(parsed.error);
    setProblem(null);
    action.mutate({ method: "PUT", path: templatePath(template ? template.name : parsed.name), json: parsed.body, success: template ? "Template saved" : "Template defined" }, { onSuccess: onClose });
  };
  return (
    <Modal title={template ? `Template ${template.name}` : "New onboarding template"} onClose={onClose}>
      <div className="form">
        <div className="grid g3">
          <Field label="Name" hint={template ? "The name is the key; to rename, define a new one" : "Letters, digits, . _ -"}>
            <input value={form.name} onChange={(e) => set("name", e.target.value)} readOnly={template !== null} autoFocus={template === null} />
          </Field>
          <Field label="Entity type" hint="For example O-DU"><input value={form.entityType} onChange={(e) => set("entityType", e.target.value)} /></Field>
          <Field label="Vendor" hint="Blank: any vendor"><input value={form.vendorName} onChange={(e) => set("vendorName", e.target.value)} /></Field>
        </div>
        <Field label="Description"><input value={form.description} maxLength={500} onChange={(e) => set("description", e.target.value)} /></Field>
        <Field label="Software baseline" hint="The software version a new element is expected to run; a different one is flagged"><input className="mono" value={form.softwareBaseline} onChange={(e) => set("softwareBaseline", e.target.value)} /></Field>
        <label className="row gap small"><input type="checkbox" checked={form.requireBaseline} onChange={(e) => set("requireBaseline", e.target.checked)} /> Stop the onboarding when the element does not run the baseline (otherwise it only flags it)</label>
        <label className="row gap small"><input type="checkbox" checked={form.autoApply} onChange={(e) => set("autoApply", e.target.checked)} /> Apply by itself when the element first reports in (otherwise an operator applies it)</label>
        <label className="row gap small"><input type="checkbox" checked={form.enabled} onChange={(e) => set("enabled", e.target.checked)} /> Enabled (a disabled template is never selected)</label>
        <Field label="Changes (JSON list)" hint="Each: managedFunctionRef (optional), attributeChanges, operation (merge, replace, create, delete, remove). The element is the new element's own, so no managedElementRef.">
          <textarea rows={9} className="mono" value={form.changes} onChange={(e) => set("changes", e.target.value)} spellCheck={false} />
        </Field>
        {problem && <div className="error-box" role="alert">{problem}</div>}
        <div className="row gap end">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="button" className="btn primary" disabled={action.isPending} onClick={save}>{action.isPending ? "…" : "Save"}</button>
        </div>
      </div>
    </Modal>
  );
}

/** What a role that may not change a template sees of it: every field, nothing to change. */
function TemplateDetails({ template }: { template: OnboardingTemplate }) {
  return (
    <KeyValue items={[
      ["Applies to", `${template.entityType} · ${template.vendorName ?? "any vendor"}`], ["Description", template.description],
      ["Software baseline", template.softwareBaseline ? `${template.softwareBaseline}${template.requireBaseline ? " (required)" : ""}` : null],
      ["Applied", template.autoApply ? "on the first heartbeat" : "by an operator"], ["State", template.enabled ? "enabled" : "disabled"],
      ["Created", formatTime(template.createdAt)], ["Last changed", formatTime(template.updatedAt)],
      ["Changes", <ul key="c" aria-label="Template changes">{template.changes.map((c, i) => (
        <li key={i} className="small"><code>{c.operation} {c.managedFunctionRef ?? "(the element)"} {JSON.stringify(c.attributeChanges)}</code></li>))}</ul>],
    ]} />
  );
}
