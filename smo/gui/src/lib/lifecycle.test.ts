import { describe, expect, it } from "vitest";

import type { CampaignStatus, OnboardingStatus, OnboardingTemplate } from "../api/types";
import {
  blankCampaign, blankTemplate, campaignActions, campaignPayload, describeHalt, describeSelector, describeSettings, onboardingActions, parseChanges, templateForm,
  templatePayload, waveProgress, type CampaignForm, type TemplateForm,
} from "./lifecycle";

const template = (over: Partial<TemplateForm> = {}): TemplateForm => ({ ...blankTemplate(), name: "du-basic", entityType: "O-DU", ...over });
const campaign = (over: Partial<CampaignForm> = {}): CampaignForm => ({ ...blankCampaign(), name: "r3", refs: ["ME-1", "ME-2"], ...over });

describe("onboardingActions", () => {
  it("offers what the onboarding state machine allows and nothing while the template is being written", () => {
    const table: Record<OnboardingStatus, { apply: boolean; select: boolean }> = {
      DISCOVERED: { apply: false, select: true }, NO_TEMPLATE: { apply: false, select: true }, TEMPLATE_SELECTED: { apply: true, select: true },
      APPLYING: { apply: false, select: false }, ONBOARDED: { apply: true, select: true }, FAILED: { apply: true, select: true },
    };
    for (const [status, expected] of Object.entries(table)) expect(onboardingActions(status as OnboardingStatus)).toEqual(expected);
  });
});

describe("parseChanges", () => {
  it("accepts a list of changes and fills in the operation", () => {
    const r = parseChanges('[{"managedFunctionRef": "NRCellDU=1", "attributeChanges": {"txPower": 20}}, {"attributeChanges": {"adminState": "unlocked"}, "operation": "replace"}]');
    expect(r).toEqual({ ok: true, value: [
      { managedFunctionRef: "NRCellDU=1", attributeChanges: { txPower: 20 }, operation: "merge" },
      { attributeChanges: { adminState: "unlocked" }, operation: "replace" },
    ] });
  });

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

  it("refuses more than 200 changes", () => {
    const r = parseChanges(JSON.stringify(Array.from({ length: 201 }, () => ({ attributeChanges: {} }))));
    expect(r).toEqual({ ok: false, error: "at most 200 changes" });
  });
});

describe("templatePayload", () => {
  it("builds the body RAN NF OAM takes, with blanks as null", () => {
    const r = templatePayload(template({ vendorName: " acme ", softwareBaseline: "2.1", requireBaseline: true, autoApply: true }), false);
    expect(r.ok && r.name).toBe("du-basic");
    expect(r.ok && r.body).toMatchObject({ entityType: "O-DU", vendorName: "acme", description: null, softwareBaseline: "2.1", requireBaseline: true, autoApply: true, enabled: true });
    expect(r.ok && (r.body.changes as unknown[]).length).toBe(1);
  });

  it("checks the name only when making a template, and needs a type and, for a required baseline, a baseline", () => {
    expect(templatePayload(template({ name: "-bad" }), false).ok).toBe(false);
    expect(templatePayload(template({ name: "has space" }), false).ok).toBe(false);
    expect(templatePayload(template({ name: "" }), true).ok).toBe(true);                              // editing: the name is the key and is not sent in the body
    expect(templatePayload(template({ entityType: " " }), false)).toMatchObject({ ok: false });
    expect(templatePayload(template({ requireBaseline: true }), false)).toEqual({ ok: false, error: "Requiring the baseline needs a software baseline" });
    expect(templatePayload(template({ changes: "[]" }), false).ok).toBe(false);
  });

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

describe("campaignPayload", () => {
  it("builds a named-elements request with the GUI's defaults and no requestedBy", () => {
    const r = campaignPayload(campaign({ softwareVersion: " 3.0 ", waveSize: "2" }));
    expect(r).toEqual({ ok: true, body: { name: "r3", softwareVersion: "3.0", waveSize: 2, wavePauseSeconds: 0, gateMaxNewAlarms: 0, onGateFailure: "halt", rollbackOrder: "reverse", managedElementRefs: ["ME-1", "ME-2"] } });
  });

  it("builds a selector request from the keys given and leaves out what is blank", () => {
    const r = campaignPayload(campaign({ mode: "selector", refs: [], region: "eu", tenant: " acme ", jobTimeoutSeconds: "900", onGateFailure: "rollback", rollbackOrder: "all" }), true);
    expect(r).toEqual({ ok: true, body: { name: "r3", wavePauseSeconds: 0, gateMaxNewAlarms: 0, onGateFailure: "rollback", rollbackOrder: "all", jobTimeoutSeconds: 900, selector: { region: "eu", tenant: "acme" }, dryRun: true } });
  });

  it.each<[Partial<CampaignForm>, string]>([
    [{ name: " " }, "needs a name"],
    [{ name: "x".repeat(201) }, "at most 200"],
    [{ softwareVersion: "v".repeat(101) }, "at most 100"],
    [{ refs: [] }, "at least one managed element"],
    [{ mode: "selector", refs: [] }, "selector names at least one"],
    [{ waveSize: "0" }, "Wave size must be between"],
    [{ waveSize: "1.5" }, "whole number"],
    [{ wavePauseSeconds: "-1" }, "pause between waves"],
    [{ gateMaxNewAlarms: "x" }, "alarms allowed"],
    [{ jobTimeoutSeconds: "0" }, "job timeout must be between"],
    [{ jobTimeoutSeconds: String(8 * 86400) }, "job timeout must be between"],
  ])("refuses %j", (over, fragment) => {
    const r = campaignPayload(campaign(over));
    expect(r.ok).toBe(false);
    expect(r.ok ? "" : r.error).toContain(fragment);
  });
});

describe("campaignActions", () => {
  const table: [CampaignStatus, string | null, Record<string, boolean>][] = [
    ["PENDING", null, { halt: false, continue: false, abort: false, rollback: false, canForce: false }],
    ["RUNNING", null, { halt: true, continue: false, abort: false, rollback: false, canForce: false }],
    ["HALTED", "GATE_FAILED", { halt: false, continue: true, abort: true, rollback: true, canForce: false }],
    ["HALTED", "OPERATOR_HALT", { halt: false, continue: true, abort: true, rollback: true, canForce: false }],
    ["HALTED", "WAVE_PAUSE", { halt: true, continue: true, abort: true, rollback: true, canForce: true }],
    ["COMPLETED", null, { halt: false, continue: false, abort: false, rollback: true, canForce: false }],
    ["ABORTED", null, { halt: false, continue: false, abort: false, rollback: true, canForce: false }],
    ["ROLLING_BACK", null, { halt: false, continue: false, abort: false, rollback: false, canForce: false }],
    ["ROLLED_BACK", null, { halt: false, continue: false, abort: false, rollback: false, canForce: false }],
    ["ROLLBACK_FAILED", null, { halt: false, continue: false, abort: false, rollback: true, canForce: false }],
  ];
  it.each(table)("%s (%s)", (status, reason, expected) => {
    expect(campaignActions(status, reason)).toEqual(expected);
  });
});

describe("the one-line descriptions", () => {
  it("say where a campaign is", () => {
    expect(waveProgress({ wave: 0, waveCount: 3 })).toBe("not started");
    expect(waveProgress({ wave: 2, waveCount: 3 })).toBe("wave 2 of 3");
    expect(describeSelector({ region: "eu", tenant: "acme" })).toBe("region eu · tenant acme");
    expect(describeSelector(null)).toBe("named elements");
  });

  it("say why a campaign is held, with the code when it is not a known one", () => {
    expect(describeHalt("GATE_FAILED")).toContain("health gate failed");
    expect(describeHalt("SOMETHING_NEW")).toBe("SOMETHING_NEW");
    expect(describeHalt(null)).toBe("");
  });

  it("list the settings of a campaign", () => {
    expect(describeSettings({ waveSize: 2, wavePauseSeconds: 60, gateMaxNewAlarms: 1, onGateFailure: "rollback", jobTimeoutSeconds: 900, rollbackOrder: "reverse" }))
      .toBe("2 per wave · 60 s pause · 1 new alarm(s) allowed · a failed gate rolls back · a job times out after 900 s · rollback last wave first");
    expect(describeSettings({ waveSize: null, wavePauseSeconds: 0, gateMaxNewAlarms: 0, onGateFailure: "halt", jobTimeoutSeconds: null, rollbackOrder: "all" }))
      .toBe("one wave · no pause · 0 new alarm(s) allowed · a failed gate halts · no job timeout · rollback all at once");
  });
});
