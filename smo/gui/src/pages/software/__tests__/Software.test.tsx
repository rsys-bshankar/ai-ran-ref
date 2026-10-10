// @vitest-environment jsdom
/** Tests of the Software page (pages/software) against a fake BFF: the campaign list renders from `/software-campaigns`, the detail of a
 * halted campaign shows its reason (GATE_FAILED) with the controls only for a role that may use them, Continue posts to the campaign's
 * route, the new-campaign form dry-runs before it starts, and the pure rules (`data/form.ts`, `campaignActions`).
 * Run: `npx vitest run src/pages/software`. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../../../auth/AuthContext";
import rules from "../../../auth/permissions.fixture.json";
import { fakeBff, mountWith, type Call } from "../../../testing/bff";
import { byText, cleanup, click, settle, type } from "../../../testing/dom";
import { campaignBody, EMPTY_FORM } from "../data/form";
import { campaignActions } from "../data/types";
import { Software } from "../index";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
beforeEach(() => { document.body.innerHTML = ""; window.location.hash = ""; });

const C1 = "11111111-2222-3333-4444-555555555555";
const CAMPAIGN = {
  campaignId: C1, status: "HALTED", wave: 3, waveCount: 9, haltedReason: "GATE_FAILED", name: "vendor-a DUs → 24.3.1", softwareVersion: "24.3.1",
  createdAt: "2026-10-09T02:00:00Z", requestedBy: "smo-gui:ana", selector: { vendorName: "vendor-a", region: "eu-west" }, elements: ["du-1", "du-2", "du-3"],
  waveSize: 1, wavePauseSeconds: 900, gateMaxNewAlarms: 0, onGateFailure: "halt",
  haltedDetail: "3 new critical or major alarm(s) on the elements of wave 3", nextWaveAt: null, finishedAt: null,
  events: [{ at: "2026-10-09T02:55:00Z", event: "WAVE_STARTED", wave: 3, detail: null, by: null }, { at: "2026-10-09T03:12:00Z", event: "HALTED", wave: 3, detail: "GATE_FAILED", by: null }],
};
const REPORT = {
  ...CAMPAIGN, summary: { elements: 3, started: 3, notReached: 0, completed: 2, failed: 1, inProgress: 0, reverted: 0 },
  waves: [
    { wave: 1, elements: ["du-1"], started: true, jobs: [{ managedElementRef: "du-1", jobId: "job-1", phase: "ACTIVATE", status: "COMPLETED", revert: null }] },
    { wave: 2, elements: ["du-2"], started: true, jobs: [{ managedElementRef: "du-2", jobId: "job-2", phase: "ACTIVATE", status: "COMPLETED", revert: null }] },
    { wave: 3, elements: ["du-3"], started: true, jobs: [{ managedElementRef: "du-3", jobId: "job-3", phase: "INSTALL", status: "FAILED", revert: null }] },
  ],
  attention: [{ managedElementRef: "du-3", problem: "software job failed in phase INSTALL" }],
};

/** A fake BFF for the page (a body with a `status` field is wrapped in `{ body }`, or the fake would take it for the HTTP status). */
function bff(role: "viewer" | "operator" | "admin") {
  return fakeBff({
    "GET /me": { username: "ana", role, csrfToken: "c", local: true, totpEnrolled: true, mfaEnrolmentRequired: false },
    "GET /permissions": { role, rules },
    "GET /summary/software": { page: "software", computedAt: "", partial: [], counts: { "campaigns.RUNNING": 2, "campaigns.HALTED": 1, "campaigns.COMPLETED": 5, "campaigns.total": 9 } },
    "GET /smo/*": (c: Call) => {
      if (c.path === "/smo/ran-nf-oam/software-campaigns") return { items: [CAMPAIGN], total: 1, limit: 25, offset: 0 };
      if (c.path === `/smo/ran-nf-oam/software-campaigns/${C1}`) return { body: CAMPAIGN };
      if (c.path === `/smo/ran-nf-oam/software-campaigns/${C1}/report`) return { body: REPORT };
      if (c.path === "/smo/ran-nf-oam/vendor-capabilities") return { items: [{ vendorName: "vendor-a" }], total: 1, limit: 100, offset: 0 };
      return { status: 404, body: { title: "NOT_FOUND" } };
    },
    "POST /smo/*": (c: Call) => ((c.body as { dryRun?: boolean })?.dryRun
      ? { body: { dryRun: true, status: "VALIDATED", waveCount: 2, waves: [["du-1", "du-2"], ["du-3"]] } }
      : { status: 202, body: { campaignId: C1, status: "RUNNING", wave: 1, waveCount: 9, haltedReason: null } }),
  });
}

/** Mounts the page as `role` at `at`; returns the mount and the recorded calls. */
const open = (role: "viewer" | "operator" | "admin", at = "/software") => { const calls = bff(role); return mountWith(<AuthProvider><Software /></AuthProvider>, { at }).then((m) => ({ ...m, calls })); };

describe("Software page", () => {
  // The list is the server table of campaigns, and the tiles read the summary.
  it("lists the campaigns from /software-campaigns and counts them from the summary", async () => {
    const { container, calls } = await open("viewer");
    await settle();
    expect(container.querySelector("[data-section='software.list']")!.textContent).toContain("vendor-a DUs → 24.3.1");
    expect(container.querySelector("[data-section='software.tiles']")!.textContent).toContain("2 · 1");
    expect(calls.some((c) => c.path === "/smo/ran-nf-oam/software-campaigns" && c.query.get("limit") !== null)).toBe(true);
  });

  // A halted campaign says why (GATE_FAILED and the gate's detail), shows its waves, its events and the elements of a wave.
  it("shows the halted reason, the waves and the elements of the campaign", async () => {
    const { container } = await open("viewer", `/software?campaign=${C1}`);
    await settle();
    const detail = container.querySelector("[data-section='software.detail']")!;
    expect(detail.textContent).toContain("Halted after wave 3 · GATE_FAILED");
    expect(detail.textContent).toContain("3 new critical or major alarm(s)");
    expect(detail.querySelectorAll(".sw-wave").length).toBe(3);
    expect(detail.querySelector(".sw-wave.gate-failed")).not.toBeNull();
    const elements = container.querySelector("[data-section='software.elements']")!;
    expect(elements.querySelector("a[href='/flows/19?subject=job-1']")).not.toBeNull();
    expect(elements.querySelector("a[href='/elements/du-1']")).not.toBeNull();
  });

  // A viewer sees no control; an operator gets Continue anyway, which posts to the continue route.
  it("offers the controls only to a role that may use them, and Continue posts to its route", async () => {
    const viewer = await open("viewer", `/software?campaign=${C1}`);
    await settle();
    expect(byText(viewer.container, "button", "Continue anyway")).toBeNull();
    cleanup();
    const op = await open("operator", `/software?campaign=${C1}`);
    await settle();
    expect(byText(op.container, "button", /Roll back waves 1–3/)).not.toBeNull();
    await click(byText(op.container, "button", "Continue anyway")!);
    await settle();
    const post = op.calls.find((c) => c.method === "POST")!;
    expect(post.path).toBe(`/smo/ran-nf-oam/software-campaigns/${C1}/continue`);
    expect(post.body).toEqual({ force: false });
  });

  // Start stays off until a dry run of the same values; the dry run shows the waves and Start sends the body without dryRun.
  // Start is offered only after a dry run of the same values, and the started body is the dry-run body without `dryRun` (no `requestedBy`).
  it("dry-runs a new campaign before starting it", async () => {
    window.location.hash = "#new";
    const { container, calls } = await open("operator");
    await settle();
    const form = container.querySelector("[data-section='software.new']")!;
    const inputs = form.querySelectorAll("input");
    await type(inputs[0] as HTMLInputElement, "upgrade");
    await type(form.querySelector("input[list='sw-vendors']") as HTMLInputElement, "vendor-a");
    expect((byText(form, "button", "Start campaign") as HTMLButtonElement).disabled).toBe(true);
    await click(byText(form, "button", "Dry run")!);
    await settle();
    expect(form.textContent).toContain("3 element(s) match · 2 wave(s) of 2 (last 1)");
    await click(byText(form, "button", "Start campaign")!);
    await settle();
    const posts = calls.filter((c) => c.method === "POST");
    expect(posts[0].body).toMatchObject({ dryRun: true, name: "upgrade", selector: { vendorName: "vendor-a" } });
    expect(posts[1].body).toEqual({ name: "upgrade", onGateFailure: "halt", rollbackOrder: "reverse", selector: { vendorName: "vendor-a" }, wavePauseSeconds: 0, gateMaxNewAlarms: 0 });
  });
});

describe("campaign rules", () => {
  // The body follows the backend's bounds: a name, a non-empty selector or element list, whole numbers.
  it("builds the campaign body or names the first problem", () => {
    expect(campaignBody(EMPTY_FORM)).toEqual({ ok: false, error: "Give the campaign a name" });
    expect(campaignBody({ ...EMPTY_FORM, name: "x" })).toMatchObject({ ok: false });
    expect(campaignBody({ ...EMPTY_FORM, name: "x", mode: "list", elements: "du-1, du-2\ndu-1", waveSize: "0" })).toEqual({ ok: false, error: "Wave size must be a whole number, at least 1" });
    expect(campaignBody({ ...EMPTY_FORM, name: "x", mode: "list", elements: "du-1, du-2\ndu-1", waveSize: "2" }))
      .toEqual({ ok: true, body: { name: "x", onGateFailure: "halt", rollbackOrder: "reverse", managedElementRefs: ["du-1", "du-2"], waveSize: 2, wavePauseSeconds: 0, gateMaxNewAlarms: 0 } });
  });

  // Each state offers what the backend accepts: a paused campaign needs force to go on early.
  it("offers the actions each campaign state accepts", () => {
    const later = new Date(Date.now() + 60_000).toISOString();
    expect(campaignActions({ status: "RUNNING", haltedReason: null, nextWaveAt: null }).map((a) => a.action)).toEqual(["halt"]);
    expect(campaignActions({ status: "HALTED", haltedReason: "WAVE_PAUSE", nextWaveAt: later })).toEqual([
      { action: "continue", force: true }, { action: "halt", force: false }, { action: "rollback", force: false }, { action: "abort", force: false }]);
    expect(campaignActions({ status: "COMPLETED", haltedReason: null, nextWaveAt: null }).map((a) => a.action)).toEqual(["rollback"]);
    expect(campaignActions({ status: "ROLLED_BACK", haltedReason: null, nextWaveAt: null })).toEqual([]);
  });

  // Table of every campaign state (and halted reason) with the actions offered, the rules RAN NF OAM's state machine accepts (ported from the
  // former lib/lifecycle.ts table when the two copies became one): nothing for a campaign that has not started or is rolling back or rolled back.
  it.each<[string, string | null, string[]]>([
    ["PENDING", null, []],
    ["RUNNING", null, ["halt"]],
    ["HALTED", "GATE_FAILED", ["continue", "rollback", "abort"]],
    ["HALTED", "OPERATOR_HALT", ["continue", "rollback", "abort"]],
    ["HALTED", "WAVE_PAUSE", ["continue", "halt", "rollback", "abort"]],
    ["COMPLETED", null, ["rollback"]],
    ["ABORTED", null, ["rollback"]],
    ["ROLLING_BACK", null, []],
    ["ROLLED_BACK", null, []],
    ["ROLLBACK_FAILED", null, ["rollback"]],
  ])("%s (%s) offers %j", (status, reason, actions) => {
    expect(campaignActions({ status: status as never, haltedReason: reason, nextWaveAt: null }).map((a) => a.action)).toEqual(actions);
  });

  // Table of invalid campaign forms (fields changed from a valid named-elements form, fragment of the message): each is refused before any call
  // with a message naming the field or the limit, the limits being RAN NF OAM's own (lifecycle.py `CampaignRequest`).
  it.each<[Partial<typeof EMPTY_FORM>, string]>([
    [{ name: " " }, "name"],
    [{ name: "x".repeat(201) }, "at most 200"],
    [{ softwareVersion: "v".repeat(101) }, "at most 100"],
    [{ elements: "" }, "at least one managed element"],
    [{ elements: Array.from({ length: 5001 }, (_, i) => `ME-${i}`).join(",") }, "at most 5000 elements"],
    [{ mode: "selector" }, "at least one of vendor"],
    [{ waveSize: "0" }, "Wave size"],
    [{ waveSize: "1.5" }, "whole number"],
    [{ wavePauseSeconds: "-1" }, "Pause between waves"],
    [{ gateMaxNewAlarms: "x" }, "Max new alarms"],
    [{ jobTimeoutSeconds: "0" }, "Job timeout"],
    [{ jobTimeoutSeconds: String(8 * 86400) }, "Job timeout must be at most"],
  ])("refuses %j", (over, fragment) => {
    const r = campaignBody({ ...EMPTY_FORM, name: "r3", mode: "list", elements: "ME-1, ME-2", ...over });
    expect(r.ok).toBe(false);
    expect(r.ok ? "" : r.error).toContain(fragment);
  });
});
