// @vitest-environment jsdom
/** Tests of what the Software page took over from the pre-redesign "Software campaigns" tab (MGT-15.5 to 15.7, PR-MGT-14.7): the new-campaign
 * form's job timeout and rollback order (sent as RAN NF OAM takes them, refused out of range before any call), the detail's settings line, a
 * job failed by the timeout marked "timed out", the rollback confirmation naming the order, and the failure-notice watchers (an admin adds
 * and removes one; an operator only reads). The BFF is `fakeBff` with the real permission table; jsdom, no server.
 * Run: `cd gui && npx vitest run src/pages/software`. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../../../auth/AuthContext";
import rules from "../../../auth/permissions.fixture.json";
import { fakeBff, mountWith, type Call } from "../../../testing/bff";
import { byText, cleanup, click, field, pick, settle, type } from "../../../testing/dom";
import { campaignBody, EMPTY_FORM } from "../data/form";
import { Software } from "../index";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
beforeEach(() => { document.body.innerHTML = ""; window.location.hash = ""; });

const C1 = "4b6f1c2e-3333-4d4e-9f5a-cccccccccccc";
/** A completed campaign with a job timeout and a reverse rollback order; its second wave's job timed out. `extra` replaces fields. */
const campaign = (extra: Record<string, unknown> = {}) => ({
  campaignId: C1, status: "ROLLBACK_FAILED", wave: 2, waveCount: 2, haltedReason: null, name: "r3", softwareVersion: "3.0", createdAt: "2026-10-09T02:00:00Z",
  requestedBy: "smo-gui:ana", selector: null, elements: ["ME-1", "ME-2"], waveSize: 1, wavePauseSeconds: 0, gateMaxNewAlarms: 0, onGateFailure: "halt",
  jobTimeoutSeconds: 3600, rollbackOrder: "reverse", haltedDetail: null, nextWaveAt: null, finishedAt: null, events: [], ...extra,
});
/** The report of `campaign()`: ME-2's job failed by the timeout and its revert failed. */
const report = () => ({
  ...campaign(), summary: { elements: 2, started: 2, notReached: 0, completed: 1, failed: 1, inProgress: 0, reverted: 1 },
  waves: [
    { wave: 1, elements: ["ME-1"], started: true, jobs: [{ managedElementRef: "ME-1", jobId: "j-1", phase: "ACTIVATE", status: "COMPLETED", revert: "COMPLETED" }] },
    { wave: 2, elements: ["ME-2"], started: true, jobs: [{ managedElementRef: "ME-2", jobId: "j-2", phase: "INSTALL", status: "FAILED", revert: "FAILED", timedOut: true }] },
  ],
  attention: [],
});

/** Installs the fake BFF for `role`: one campaign with its report, one watcher, and the POST / DELETE routes; returns the recorded calls. */
function bff(role: "viewer" | "operator" | "admin") {
  return fakeBff({
    "GET /me": { username: "ana", role, csrfToken: "c", local: true, totpEnrolled: true, mfaEnrolmentRequired: false },
    "GET /permissions": { role, rules },
    "GET /summary/software": { page: "software", computedAt: "", partial: [], counts: { "campaigns.total": 1 } },
    "GET /smo/*": (c: Call) => {
      if (c.path === "/smo/ran-nf-oam/software-campaigns") return { items: [campaign()], total: 1, limit: 25, offset: 0 };
      if (c.path === `/smo/ran-nf-oam/software-campaigns/${C1}`) return { body: campaign() };
      if (c.path === `/smo/ran-nf-oam/software-campaigns/${C1}/report`) return { body: report() };
      if (c.path === "/smo/ran-nf-oam/lifecycle-subscriptions") return { items: [{ subscriptionId: "s-1", callbackUri: "https://noc.example/hook", events: ["CAMPAIGN_HALTED"], createdAt: null }], limit: 200, offset: 0 };
      if (c.path === "/smo/ran-nf-oam/vendor-capabilities") return { items: [], total: 0, limit: 100, offset: 0 };
      return { status: 404, body: { title: "NOT_FOUND" } };
    },
    "POST /smo/*": (c: Call) => ((c.body as { dryRun?: boolean })?.dryRun
      ? { body: { dryRun: true, status: "VALIDATED", waveCount: 1, waves: [["ME-1"]] } }
      : { status: 201, body: { campaignId: C1, status: "RUNNING", wave: 1, waveCount: 1, haltedReason: null } }),
    "DELETE /smo/*": { status: 204 },
  });
}

/** Mounts the Software page as `role` at `at`. */
const open = (role: "viewer" | "operator" | "admin", at = "/software") => { const calls = bff(role); return mountWith(<AuthProvider><Software /></AuthProvider>, { at }).then((m) => ({ ...m, calls })); };

describe("job timeout and rollback order (MGT-15.6, 15.7)", () => {
  // The body carries the job timeout only when one is given, always the rollback order, and a timeout over seven days or not whole is refused before any call.
  it("builds the body with the job timeout and the rollback order, and refuses a timeout out of range", () => {
    const named = { ...EMPTY_FORM, name: "r3", mode: "list" as const, elements: "ME-1" };
    expect(campaignBody(named)).toMatchObject({ ok: true, body: { rollbackOrder: "reverse" } });
    expect((campaignBody(named) as { body: Record<string, unknown> }).body).not.toHaveProperty("jobTimeoutSeconds");
    expect(campaignBody({ ...named, jobTimeoutSeconds: "600", rollbackOrder: "all" })).toMatchObject({ ok: true, body: { jobTimeoutSeconds: 600, rollbackOrder: "all" } });
    expect(campaignBody({ ...named, jobTimeoutSeconds: "0" })).toEqual({ ok: false, error: "Job timeout must be a whole number, at least 1" });
    expect(campaignBody({ ...named, jobTimeoutSeconds: String(7 * 86400 + 1) })).toEqual({ ok: false, error: "Job timeout must be at most 604800" });
  });

  // The form's job timeout and rollback order reach the dry run and the start as typed and chosen.
  it("sends the job timeout and the rollback order the operator chose", async () => {
    window.location.hash = "#new";
    const { container, calls } = await open("operator");
    await settle();
    const form = container.querySelector("[data-section='software.new']") as HTMLElement;
    await type(field(form, "Name"), "r3");
    await type(form.querySelector("input[list='sw-vendors']") as HTMLInputElement, "acme");
    await type(field(form, "Job timeout, seconds"), "900");
    await pick(field<HTMLSelectElement>(form, "A rollback undoes"), "all");
    await click(byText(form, "button", "Dry run")!);
    await settle();
    await click(byText(form, "button", "Start campaign")!);
    await settle();
    const started = calls.filter((c) => c.method === "POST")[1];
    expect(started.body).toMatchObject({ name: "r3", jobTimeoutSeconds: 900, rollbackOrder: "all", selector: { vendorName: "acme" } });
    expect(started.body).not.toHaveProperty("requestedBy");                        // the BFF sets who asked
  });

  // The detail says how long a job may run and in which order a rollback undoes the waves, and a job failed by the timeout is marked so.
  it("shows the timeout and rollback order, and marks a timed-out job", async () => {
    const { container } = await open("viewer", `/software?campaign=${C1}&wave=2`);
    await settle(8);
    const detail = container.querySelector("[data-section='software.detail']")!;
    expect(detail.textContent).toContain("a job times out after");
    expect(detail.textContent).toContain("rollback: last wave first");
    const elements = container.querySelector("[data-section='software.elements']")!;
    expect(elements.textContent).toContain("timed out");
  });

  // Rolling back again after a failed rollback asks first and names the order (last wave first), and a declined question sends nothing.
  it("names the rollback order in the confirmation, and sends nothing when declined", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    const { container } = await open("operator", `/software?campaign=${C1}`);
    await settle(8);
    const detail = container.querySelector("[data-section='software.detail']") as HTMLElement;
    await click(byText(detail, "button", /^Roll back/)!);
    await settle();
    expect(confirm.mock.calls[0][0]).toContain("the last wave first");
  });
});

describe("who is told about failures (PR-MGT-14.7, MGT-15.6)", () => {
  // An admin adds a watcher with the trimmed URL and only the ticked events, and removes one with a DELETE of its id.
  it("lists the watchers, and an admin adds one for chosen events and removes one", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const { container, calls } = await open("admin");
    await settle();
    const box = container.querySelector("[data-section='software.watchers']") as HTMLElement;
    expect(box.textContent).toContain("https://noc.example/hook");
    await click(byText(box, "button", "Add watcher")!);
    await settle();
    const dialog = document.querySelector("[role=dialog]") as HTMLElement;
    await type(field(dialog, "Callback URL"), " https://ops.example/events ");
    await click(Array.from(dialog.querySelectorAll("input[type=checkbox]"))[1] as HTMLElement);         // CAMPAIGN_HALTED
    await click(byText(dialog, "button", "Add watcher")!);
    await settle();
    expect(calls.find((c) => c.method === "POST")!.body).toEqual({ callbackUri: "https://ops.example/events", events: ["CAMPAIGN_HALTED"] });
    await click(byText(box, "button", "Remove")!);
    await settle();
    expect(calls.find((c) => c.method === "DELETE")!.path).toBe("/smo/ran-nf-oam/lifecycle-subscriptions/s-1");
  });

  // An operator sees the watchers but no Add or Remove button: subscriptions are an admin's.
  it("gives an operator the list and no way to change it", async () => {
    const { container } = await open("operator");
    await settle();
    const box = container.querySelector("[data-section='software.watchers']") as HTMLElement;
    expect(box.textContent).toContain("https://noc.example/hook");
    expect(byText(box, "button", "Add watcher")).toBeNull();
    expect(byText(box, "button", "Remove")).toBeNull();
  });
});
