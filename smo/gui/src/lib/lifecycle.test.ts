/**
 * Unit tests of lib/lifecycle.ts: the button rules of onboarding and the template form turned into a request body with its validation messages (the
 * campaign rules are tested with the Software page, pages/software/__tests__). Pure functions, no fixtures and no network.
 * Run: `cd gui && npx vitest run src/lib/lifecycle.test.ts`.
 */
import { describe, expect, it } from "vitest";

import type { OnboardingStatus, OnboardingTemplate } from "../api/types";
import { blankTemplate, onboardingActions, parseChanges, templateForm, templatePayload, type TemplateForm } from "./lifecycle";

const template = (over: Partial<TemplateForm> = {}): TemplateForm => ({ ...blankTemplate(), name: "du-basic", entityType: "O-DU", ...over });

describe("onboardingActions", () => {
  // A button the backend would refuse as an illegal transition is never offered: nothing while APPLYING, apply only once a template is chosen or an apply has ended.
  it("offers what the onboarding state machine allows and nothing while the template is being written", () => {
    const table: Record<OnboardingStatus, { apply: boolean; select: boolean }> = {
      DISCOVERED: { apply: false, select: true }, NO_TEMPLATE: { apply: false, select: true }, TEMPLATE_SELECTED: { apply: true, select: true },
      APPLYING: { apply: false, select: false }, ONBOARDED: { apply: true, select: true }, FAILED: { apply: true, select: true },
    };
    for (const [status, expected] of Object.entries(table)) expect(onboardingActions(status as OnboardingStatus)).toEqual(expected);
  });
});

describe("parseChanges", () => {
  // A valid change list is accepted and a missing operation defaults to merge, as RAN NF OAM does.
  it("accepts a list of changes and fills in the operation", () => {
    const r = parseChanges('[{"managedFunctionRef": "NRCellDU=1", "attributeChanges": {"txPower": 20}}, {"attributeChanges": {"adminState": "unlocked"}, "operation": "replace"}]');
    expect(r).toEqual({ ok: true, value: [
      { managedFunctionRef: "NRCellDU=1", attributeChanges: { txPower: 20 }, operation: "merge" },
      { attributeChanges: { adminState: "unlocked" }, operation: "replace" },
    ] });
  });

  // Table of malformed change lists (text, fragment of the message): each is refused and the message names the problem.
  it.each([
    ["not json", "not JSON"],
    ["{}", "JSON list"],
    ["[]", "JSON list"],
    ["[1]", "change 1 must be an object"],
    ['[{"managedElementRef": "ME-1", "attributeChanges": {}}]', "managedElementRef"],
    ['[{"attributeChanges": {}, "bogus": 1}]', "unknown field: bogus"],
    ['[{"attributeChanges": []}]', "attributeChanges must be an object"],
    ['[{"attributeChanges": {}, "operation": "wipe"}]', "operation is one of"],
    ['[{"attributeChanges": {}, "managedFunctionRef": 5}]', "managedFunctionRef"],
  ])("refuses %s", (text, fragment) => {
    const r = parseChanges(text);
    expect(r.ok).toBe(false);
    expect(r.ok ? "" : r.error).toContain(fragment);
  });

  // A template with more than 200 changes is refused before the call, the same limit RAN NF OAM enforces.
  it("refuses more than 200 changes", () => {
    const r = parseChanges(JSON.stringify(Array.from({ length: 201 }, () => ({ attributeChanges: {} }))));
    expect(r).toEqual({ ok: false, error: "at most 200 changes" });
  });
});

describe("templatePayload", () => {
  // The template form becomes the PUT body with trimmed values and blank vendor and description sent as null, not as empty text.
  it("builds the body RAN NF OAM takes, with blanks as null", () => {
    const r = templatePayload(template({ vendorName: " acme ", softwareBaseline: "2.1", requireBaseline: true, autoApply: true }), false);
    expect(r.ok && r.name).toBe("du-basic");
    expect(r.ok && r.body).toMatchObject({ entityType: "O-DU", vendorName: "acme", description: null, softwareBaseline: "2.1", requireBaseline: true, autoApply: true, enabled: true });
    expect(r.ok && (r.body.changes as unknown[]).length).toBe(1);
  });

  // The name is validated only when creating (when editing it is the key), and a missing type, a required baseline without a baseline, or no changes is refused.
  it("checks the name only when making a template, and needs a type and, for a required baseline, a baseline", () => {
    expect(templatePayload(template({ name: "-bad" }), false).ok).toBe(false);
    expect(templatePayload(template({ name: "has space" }), false).ok).toBe(false);
    expect(templatePayload(template({ name: "" }), true).ok).toBe(true);                              // editing: the name is the key and is not sent in the body
    expect(templatePayload(template({ entityType: " " }), false)).toMatchObject({ ok: false });
    expect(templatePayload(template({ requireBaseline: true }), false)).toEqual({ ok: false, error: "Requiring the baseline needs a software baseline" });
    expect(templatePayload(template({ changes: "[]" }), false).ok).toBe(false);
  });

  // Loading a stored template into the form and saving it unchanged sends back the same values, so an edit does not silently alter a field.
  it("round-trips a stored template through the form", () => {
    const stored: OnboardingTemplate = {
      name: "du", description: "basic", entityType: "O-DU", vendorName: null, softwareBaseline: "1.0", requireBaseline: false, autoApply: true, enabled: false,
      changes: [{ attributeChanges: { a: 1 }, operation: "merge" }], createdAt: null, updatedAt: null,
    };
    const form = templateForm(stored);
    const r = templatePayload(form, true);
    expect(r.ok && r.body).toEqual({ entityType: "O-DU", vendorName: null, description: "basic", changes: stored.changes, softwareBaseline: "1.0", requireBaseline: false, autoApply: true, enabled: false });
  });
});
