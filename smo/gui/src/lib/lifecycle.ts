/**
 * What the onboarding and software campaign pages (MGT-14.6, MGT-15.5) decide without a screen: the wording of each state, which actions a state allows (the
 * ones RAN NF OAM's state machines allow, so a button is never offered that the backend would refuse as an illegal transition), and the forms turned into the
 * request bodies RAN NF OAM validates (the same limits, so a mistake is shown before the call). Who asked (`requestedBy`) is never sent: the GUI backend sets it.
 * Used by pages/Onboarding.tsx and pages/Campaigns.tsx; pure functions only, no network and no React, so lifecycle.test.ts covers it without a screen.
 * A limit changed in RAN NF OAM's validation must be changed here as well, or the form accepts what the backend then refuses.
 */

import type { CampaignStatus, OnboardingStatus, OnboardingTemplate, TemplateChange } from "../api/types";

export const ONBOARDING_MEANING: Record<OnboardingStatus, string> = {
  DISCOVERED: "Registered; not yet matched against the templates",
  NO_TEMPLATE: "Templates exist but none is enabled for this element's type and vendor",
  TEMPLATE_SELECTED: "A template is chosen and waits to be applied",
  APPLYING: "The template is being written to the element",
  ONBOARDED: "The template was written",
  FAILED: "The template could not be written; the detail says why",
};

export const CAMPAIGN_MEANING: Record<CampaignStatus, string> = {
  PENDING: "Created, not started",
  RUNNING: "A wave is in progress; the next starts when every job of this one has ended and the health gate passes",
  HALTED: "Held between waves; an operator decides what happens next",
  COMPLETED: "Every wave ran and the last gate passed or was overridden",
  ABORTED: "Ended by an operator before the last wave; the waves that ran stay as they are",
  ROLLING_BACK: "Revert jobs are running",
  ROLLED_BACK: "Every completed job has been reverted",
  ROLLBACK_FAILED: "A revert job failed; roll back again to retry those",
};

export const HALT_MEANING: Record<string, string> = {
  GATE_FAILED: "A health gate failed: a software job failed or timed out, or more new critical or major alarms than allowed",
  WAVE_PAUSE: "The pause between waves; it continues by itself when the time has passed",
  OPERATOR_HALT: "An operator halted it",
};

/** The text of a halted campaign's reason for a table cell: the meaning when it is a known one. */
export function describeHalt(reason: string | null | undefined): string {
  return reason ? HALT_MEANING[reason] ?? reason : "";
}

// ---------------------------------------------------------------- onboarding of an element

/** Which buttons an element's onboarding row offers. Applying is possible once a template is selected, and again after it succeeded or failed; selecting (again)
 * is possible wherever the template is not being written. */
export function onboardingActions(status: OnboardingStatus): { apply: boolean; select: boolean } {
  return { apply: status === "TEMPLATE_SELECTED" || status === "ONBOARDED" || status === "FAILED", select: status !== "APPLYING" };
}

// ---------------------------------------------------------------- templates

export const TEMPLATE_NAME = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/;
const OPERATIONS = ["merge", "replace", "create", "delete", "remove"];

/** The template editor's fields as the form holds them: every field is text or a flag, and `changes` is the JSON the person edits, not yet parsed. */
export interface TemplateForm {
  name: string; entityType: string; vendorName: string; description: string; softwareBaseline: string;
  requireBaseline: boolean; autoApply: boolean; enabled: boolean; changes: string;
}

export const STARTER_CHANGES = JSON.stringify([{ managedFunctionRef: "GNBDUFunction=1", attributeChanges: { administrativeState: "UNLOCKED" }, operation: "merge" }], null, 2);

export function blankTemplate(): TemplateForm {
  return { name: "", entityType: "", vendorName: "", description: "", softwareBaseline: "", requireBaseline: false, autoApply: false, enabled: true, changes: STARTER_CHANGES };
}

/** Turns a stored template into the editor's fields (null becomes a blank, the changes become indented JSON), so editing a template and saving it unchanged sends back what it held. */
export function templateForm(t: OnboardingTemplate): TemplateForm {
  return {
    name: t.name, entityType: t.entityType, vendorName: t.vendorName ?? "", description: t.description ?? "", softwareBaseline: t.softwareBaseline ?? "",
    requireBaseline: t.requireBaseline, autoApply: t.autoApply, enabled: t.enabled, changes: JSON.stringify(t.changes, null, 2),
  };
}

/** The changes of a template from the editor's JSON: a non-empty list of up to 200 changes, each an object with `attributeChanges` (an object), optionally a
 * `managedFunctionRef` and an `operation`; a change names no `managedElementRef` (the element is the new element's own). */
export function parseChanges(text: string): { ok: true; value: TemplateChange[] } | { ok: false; error: string } {
  let parsed: unknown;
  try { parsed = JSON.parse(text); } catch (e) { return { ok: false, error: `the changes are not JSON: ${(e as Error).message}` }; }
  if (!Array.isArray(parsed) || parsed.length === 0) return { ok: false, error: "the changes are a JSON list with at least one change" };
  if (parsed.length > 200) return { ok: false, error: "at most 200 changes" };
  const out: TemplateChange[] = [];
  for (const [i, c] of parsed.entries()) {
    const at = `change ${i + 1}`;
    if (c === null || typeof c !== "object" || Array.isArray(c)) return { ok: false, error: `${at} must be an object` };
    const o = c as Record<string, unknown>;
    if ("managedElementRef" in o) return { ok: false, error: `${at} names a managedElementRef: a template applies to the new element, so leave it out` };
    const unknown = Object.keys(o).filter((k) => !["managedFunctionRef", "attributeChanges", "operation"].includes(k));
    if (unknown.length) return { ok: false, error: `${at} has an unknown field: ${unknown[0]}` };
    const attrs = o.attributeChanges ?? {};
    if (attrs === null || typeof attrs !== "object" || Array.isArray(attrs)) return { ok: false, error: `${at}: attributeChanges must be an object` };
    const operation = o.operation ?? "merge";
    if (typeof operation !== "string" || !OPERATIONS.includes(operation)) return { ok: false, error: `${at}: operation is one of ${OPERATIONS.join(", ")}` };
    const fn = o.managedFunctionRef;
    if (fn !== undefined && fn !== null && (typeof fn !== "string" || fn.length > 500)) return { ok: false, error: `${at}: managedFunctionRef is a text of at most 500 characters` };
    out.push({ ...(typeof fn === "string" && fn ? { managedFunctionRef: fn } : {}), attributeChanges: attrs as Record<string, unknown>, operation });
  }
  return { ok: true, value: out };
}

/** The body of PUT /ran-nf-oam/onboarding-templates/{name}, or the problem to show. `editing` leaves the name alone (it is the key). */
export function templatePayload(form: TemplateForm, editing: boolean): { ok: true; name: string; body: Record<string, unknown> } | { ok: false; error: string } {
  const name = form.name.trim();
  if (!editing && !TEMPLATE_NAME.test(name)) return { ok: false, error: "A template name is 1 to 100 letters, digits, '.', '_' or '-', starting with a letter or digit" };
  if (!form.entityType.trim()) return { ok: false, error: "The entity type is needed (for example O-DU)" };
  const baseline = form.softwareBaseline.trim();
  if (form.requireBaseline && !baseline) return { ok: false, error: "Requiring the baseline needs a software baseline" };
  const changes = parseChanges(form.changes);
  if (!changes.ok) return changes;
  return {
    ok: true, name,
    body: {
      entityType: form.entityType.trim(), vendorName: form.vendorName.trim() || null, description: form.description.trim() || null, changes: changes.value,
      softwareBaseline: baseline || null, requireBaseline: form.requireBaseline, autoApply: form.autoApply, enabled: form.enabled,
    },
  };
}

// ---------------------------------------------------------------- software campaigns

/** The start-campaign form's fields as text and flags; `mode` says whether `refs` (named elements) or the type, vendor, region and tenant fields (a selector) choose the elements. */
export interface CampaignForm {
  name: string; softwareVersion: string; mode: "named" | "selector"; refs: string[];
  entityType: string; vendorName: string; region: string; tenant: string;
  waveSize: string; wavePauseSeconds: string; gateMaxNewAlarms: string; onGateFailure: "halt" | "rollback"; jobTimeoutSeconds: string; rollbackOrder: "all" | "reverse";
}

/** The GUI starts with the rollback order the platform recommends for a rollout in waves (last wave first); the API's own default stays "all". */
export function blankCampaign(): CampaignForm {
  return {
    name: "", softwareVersion: "", mode: "named", refs: [], entityType: "", vendorName: "", region: "", tenant: "",
    waveSize: "", wavePauseSeconds: "0", gateMaxNewAlarms: "0", onGateFailure: "halt", jobTimeoutSeconds: "", rollbackOrder: "reverse",
  };
}

/**
 * Reads a whole-number field: a blank is accepted as "not set" (`value: null`), anything else must be an integer within `min` to `max`, or the error names the
 * field (`name`) and the limit. Has no side effect.
 */
function whole(text: string, name: string, min: number, max: number): { ok: true; value: number | null } | { ok: false; error: string } {
  const raw = text.trim();
  if (raw === "") return { ok: true, value: null };
  const n = Number(raw);
  if (!Number.isInteger(n)) return { ok: false, error: `${name} must be a whole number` };
  if (n < min || n > max) return { ok: false, error: `${name} must be between ${min} and ${max}` };
  return { ok: true, value: n };
}

export const MAX_JOB_TIMEOUT_SECONDS = 7 * 86400;

/** The body of POST /ran-nf-oam/software-campaigns, or the problem to show. `dryRun` asks for the waves only. */
export function campaignPayload(form: CampaignForm, dryRun = false): { ok: true; body: Record<string, unknown> } | { ok: false; error: string } {
  const name = form.name.trim();
  if (!name) return { ok: false, error: "The campaign needs a name" };
  if (name.length > 200) return { ok: false, error: "The name is at most 200 characters" };
  if (form.softwareVersion.trim().length > 100) return { ok: false, error: "The software version is at most 100 characters" };
  const size = whole(form.waveSize, "Wave size", 1, 5000);
  const pause = whole(form.wavePauseSeconds, "The pause between waves", 0, 30 * 86400);
  const alarms = whole(form.gateMaxNewAlarms, "The alarms allowed", 0, 100000);
  const timeout = whole(form.jobTimeoutSeconds, "The job timeout", 1, MAX_JOB_TIMEOUT_SECONDS);
  if (!size.ok) return size;
  if (!pause.ok) return pause;
  if (!alarms.ok) return alarms;
  if (!timeout.ok) return timeout;
  const body: Record<string, unknown> = { name, wavePauseSeconds: pause.value ?? 0, gateMaxNewAlarms: alarms.value ?? 0, onGateFailure: form.onGateFailure, rollbackOrder: form.rollbackOrder };
  if (form.softwareVersion.trim()) body.softwareVersion = form.softwareVersion.trim();
  if (size.value !== null) body.waveSize = size.value;
  if (timeout.value !== null) body.jobTimeoutSeconds = timeout.value;
  if (form.mode === "named") {
    if (form.refs.length === 0) return { ok: false, error: "Choose at least one managed element, or select them by type, vendor, region or tenant" };
    body.managedElementRefs = form.refs;
  } else {
    const selector = Object.fromEntries(([["entityType", form.entityType], ["vendorName", form.vendorName], ["region", form.region], ["tenant", form.tenant]] as const)
      .map(([k, v]) => [k, v.trim()] as const).filter(([, v]) => v));
    if (Object.keys(selector).length === 0) return { ok: false, error: "A selector names at least one of entity type, vendor, region and tenant" };
    body.selector = selector;
  }
  if (dryRun) body.dryRun = true;
  return { ok: true, body };
}

export interface CampaignActionsAllowed { halt: boolean; continue: boolean; abort: boolean; rollback: boolean; /** continue has a pause to skip */ canForce: boolean }

/** Which of halt, continue, abort and roll back RAN NF OAM accepts for a campaign in this state (anything else is an illegal transition). A halt of a campaign
 * held by its pause turns the pause into an operator's halt; a halt of one held for another reason changes nothing, so it is not offered. */
export function campaignActions(status: CampaignStatus, haltedReason: string | null | undefined): CampaignActionsAllowed {
  const halted = status === "HALTED";
  return {
    halt: status === "RUNNING" || (halted && haltedReason === "WAVE_PAUSE"),
    continue: halted,
    abort: halted,
    rollback: halted || status === "COMPLETED" || status === "ABORTED" || status === "ROLLBACK_FAILED",
    canForce: halted && haltedReason === "WAVE_PAUSE",
  };
}

/** "wave 2 of 4" for a table cell; a campaign that has not started a wave shows "not started". */
export function waveProgress(c: { wave: number; waveCount: number }): string {
  return c.wave > 0 ? `wave ${c.wave} of ${c.waveCount}` : "not started";
}

/** A campaign's selector in one line ("region eu · tenant acme"); a campaign that names its elements has no selector and reads "named elements". */
export function describeSelector(selector: Record<string, string> | null | undefined): string {
  const parts = Object.entries(selector ?? {}).map(([k, v]) => `${k} ${v}`);
  return parts.length ? parts.join(" · ") : "named elements";
}

/** A campaign's wave settings in one line, for the detail view. */
export function describeSettings(c: { waveSize: number | null; wavePauseSeconds: number; gateMaxNewAlarms: number; onGateFailure: string; jobTimeoutSeconds: number | null; rollbackOrder: string }): string {
  return [
    c.waveSize ? `${c.waveSize} per wave` : "one wave",
    c.wavePauseSeconds ? `${c.wavePauseSeconds} s pause` : "no pause",
    `${c.gateMaxNewAlarms} new alarm(s) allowed`,
    c.onGateFailure === "rollback" ? "a failed gate rolls back" : "a failed gate halts",
    c.jobTimeoutSeconds ? `a job times out after ${c.jobTimeoutSeconds} s` : "no job timeout",
    c.rollbackOrder === "reverse" ? "rollback last wave first" : "rollback all at once",
  ].join(" · ");
}
