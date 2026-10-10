// @vitest-environment jsdom
/**
 * Component tests of the campaigns tab (pages/Campaigns.tsx): the list and its filter, the start form with its preview and its refusals, and the drawer with the actions
 * each campaign state offers, for a viewer and an operator. The backend is a fake `fakeBff` (testing/bff.ts) answering by method and path under /smo, and the
 * permissions come from auth/permissions.fixture.json; jsdom, no server. Run: `cd gui && npx vitest run src/pages/Campaigns.test.tsx`.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../auth/AuthContext";
import rules from "../auth/permissions.fixture.json";
import { fakeBff, mountWith, type Call } from "../testing/bff";
import { byText, cleanup, click, field, pick, settle, type } from "../testing/dom";
import { CampaignsTab } from "./Campaigns";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });
beforeEach(() => { document.body.innerHTML = ""; window.location.hash = ""; });

const CID = "4b6f1c2e-3333-4d4e-9f5a-cccccccccccc";
const OTHER = "9a8b7c6d-4444-4e5f-8a6b-dddddddddddd";

const summary = (id: string, over: Record<string, unknown> = {}) => ({
  campaignId: id, status: "RUNNING", wave: 1, waveCount: 2, haltedReason: null, name: id === CID ? "r3-upgrade" : "r4-upgrade", softwareVersion: "3.0", createdAt: "2026-10-09T10:00:00Z", ...over,
});
const job = (ref: string, over: Record<string, unknown> = {}) => ({ managedElementRef: ref, jobId: `job-${ref}`, phase: "ACTIVATE", status: "COMPLETED", revert: null, ...over });
/** A campaign report as RAN NF OAM returns it: a running two-wave campaign of four elements, the first wave completed; `over` replaces fields. */
const report = (over: Record<string, unknown> = {}) => ({
  ...summary(CID), requestedBy: "smo-gui:ana", selector: null, elements: ["ME-1", "ME-2", "ME-3", "ME-4"], waveSize: 2, wavePauseSeconds: 0, gateMaxNewAlarms: 0, onGateFailure: "halt",
  jobTimeoutSeconds: 900, rollbackOrder: "reverse", haltedDetail: null, nextWaveAt: null, finishedAt: null,
  events: [{ at: "2026-10-09T10:00:00Z", event: "STARTED", wave: 0, detail: "4 element(s) in 2 wave(s)", by: "smo-gui:ana" }, { at: "2026-10-09T10:00:01Z", event: "WAVE_STARTED", wave: 1, detail: "2 element(s)", by: null }],
  summary: { elements: 4, started: 2, notReached: 2, completed: 2, failed: 0, inProgress: 0, reverted: 0 },
  waves: [{ wave: 1, elements: ["ME-1", "ME-2"], started: true, jobs: [job("ME-1"), job("ME-2")] }, { wave: 2, elements: ["ME-3", "ME-4"], started: false, jobs: [] }],
  attention: [], ...over,
});

/** Installs the fake backend for a signed-in `role`: the two campaigns of the list, `detail` as the report of campaign CID, no watchers, three endpoints; `overrides` replace or add routes. Returns the recorded calls. */
function bff(role: "viewer" | "operator" | "admin", detail: Record<string, unknown> = report(), overrides: Record<string, unknown> = {}) {
  return fakeBff({
    "GET /me": { username: "ana", role, csrfToken: "c", local: true, totpEnrolled: true, mfaEnrolmentRequired: false },
    "GET /permissions": { role, rules },
    "GET /smo/ran-nf-oam/software-campaigns": { items: [summary(CID), summary(OTHER, { status: "HALTED", haltedReason: "GATE_FAILED", wave: 2 })], limit: 200, offset: 0 },
    [`GET /smo/ran-nf-oam/software-campaigns/${CID}/report`]: "body" in detail ? detail : { body: detail },   // a report has a `status` of its own, which the fake would take for the HTTP status
    "GET /smo/ran-nf-oam/lifecycle-subscriptions": { items: [], limit: 200, offset: 0 },
    "GET /smo/ran-nf-oam/o1-adaptor-endpoints": { items: ["ME-1", "ME-2", "ME-3"].map((ref) => ({ endpointId: `e-${ref}`, managedElementRef: ref, adaptorUri: "http://a", protocolSupport: ["NETCONF"], registeredVia: "x", healthStatus: "ACTIVE", lastHeartbeatAt: null })), limit: 200, offset: 0 },
    ...overrides,
  });
}

const open = () => mountWith(<AuthProvider><CampaignsTab /></AuthProvider>);
const rowOf = (container: HTMLElement, text: string) => Array.from(container.querySelectorAll("tbody tr")).find((r) => r.textContent?.includes(text)) as HTMLElement;
const posts = (calls: Call[]) => calls.filter((c) => c.method === "POST");

describe("the campaign list", () => {
  // A campaign row shows its state, wave progress and software, and a halted one says in words why it is held, not just the code.
  it("lists each campaign with its state, progress and why a halted one is held", async () => {
    bff("viewer");
    const { container } = await open();
    await settle();
    const running = rowOf(container, "r3-upgrade");
    expect(running.textContent).toContain("RUNNING");
    expect(running.textContent).toContain("wave 1 of 2");
    expect(running.textContent).toContain("3.0");
    const halted = rowOf(container, "r4-upgrade");
    expect(halted.textContent).toContain("HALTED");
    expect(halted.textContent).toContain("A health gate failed");
  });

  // An empty list says so, a failed read shows its error and not an empty table, and the state filter is sent to the backend.
  it("says when there is none, shows an error rather than an empty list, and filters by state", async () => {
    const calls = bff("viewer", report(), { "GET /smo/ran-nf-oam/software-campaigns": { items: [], limit: 200, offset: 0 } });
    const { container } = await open();
    await settle();
    expect(container.textContent).toContain("No campaign has been started.");
    await pick(container.querySelector("select[aria-label='Campaign status']") as HTMLSelectElement, "HALTED");
    await settle();
    expect(calls.filter((c) => c.path === "/smo/ran-nf-oam/software-campaigns").some((c) => c.query.get("status") === "HALTED")).toBe(true);
    cleanup();
    bff("viewer", report(), { "GET /smo/ran-nf-oam/software-campaigns": { status: 503, body: { title: "ENDPOINT_UNREACHABLE", detail: "RAN NF OAM is down" } } });
    const broken = await open();
    await settle();
    expect(broken.container.textContent).toContain("RAN NF OAM is down");
  });

  // A viewer, who may not POST campaigns, is not offered the Start button.
  it("gives a viewer no way to start one", async () => {
    bff("viewer");
    const { container } = await open();
    await settle();
    expect(byText(container, "button", "Start a campaign…")).toBeNull();
  });
});

describe("starting a campaign", () => {
  const start = async (calls: () => void = () => {}) => {
    const { container } = await open();
    await settle();
    calls();
    await click(byText(container, "button", "Start a campaign…")!);
    await settle();
    return { container, dialog: document.querySelector("[role=dialog]") as HTMLElement };
  };

  // Starting for named elements posts the chosen settings without requestedBy or dryRun (the backend sets who asked) and then opens the new campaign's detail.
  it("starts one for named elements, sends the settings and not who asked, and opens the new campaign", async () => {
    const calls = bff("operator", report(), { "POST /smo/ran-nf-oam/software-campaigns": { status: 202, body: summary(CID) } });
    const { dialog } = await start();
    await type(field(dialog, "Name"), "r3-upgrade");
    await type(field(dialog, "Software version"), "3.0");
    const select = field<HTMLSelectElement>(dialog, "Managed elements");
    for (const o of Array.from(select.options)) o.selected = o.value !== "ME-3";
    await pick(select, "ME-1");                                                                  // fires change; both selected options are read
    await type(field(dialog, "Wave size"), "2");
    await type(field(dialog, "Job timeout"), "900");
    await click(byText(dialog, "button", "Start")!);
    await settle();
    const post = posts(calls)[0];
    expect(post.path).toBe("/smo/ran-nf-oam/software-campaigns");
    expect(post.body).toMatchObject({ name: "r3-upgrade", softwareVersion: "3.0", waveSize: 2, jobTimeoutSeconds: 900, wavePauseSeconds: 0, gateMaxNewAlarms: 0, onGateFailure: "halt", rollbackOrder: "reverse" });
    expect((post.body as { managedElementRefs: string[] }).managedElementRefs.length).toBeGreaterThan(0);
    expect(post.body).not.toHaveProperty("requestedBy");
    expect(post.body).not.toHaveProperty("dryRun");
    expect(document.body.textContent).toContain("Campaign r3-upgrade");                           // the detail of the campaign it made is open
  });

  // A selector preview posts with dryRun, shows the waves, leaves the form open (nothing started), and a later edit removes the now stale preview.
  it("selects elements by type, vendor, region or tenant and previews the waves without starting anything", async () => {
    const calls = bff("operator", report(), { "POST /smo/ran-nf-oam/software-campaigns": (c: Call) => (c.body as { dryRun?: boolean }).dryRun
      ? { body: { dryRun: true, status: "VALIDATED", waveCount: 2, waves: [["ME-1", "ME-2"], ["ME-3"]] } } : { status: 202, body: summary(CID) } });
    const { dialog } = await start();
    await type(field(dialog, "Name"), "eu-rollout");
    await click(dialog.querySelector("input[type=radio][name=campaign-mode]:not(:checked)") as HTMLElement);
    await type(field(dialog, "Region"), "eu-west");
    await type(field(dialog, "Wave size"), "2");
    await click(byText(dialog, "button", "Preview waves")!);
    await settle();
    const preview = dialog.querySelector("[aria-label=Waves]")!;
    expect(preview.textContent).toContain("2 wave(s)");
    expect(preview.textContent).toContain("Wave 1: ME-1, ME-2");
    expect(preview.textContent).toContain("Wave 2: ME-3");
    expect(posts(calls)).toHaveLength(1);
    expect(posts(calls)[0].body).toMatchObject({ selector: { region: "eu-west" }, dryRun: true, waveSize: 2 });
    expect(document.querySelector("[role=dialog]")).not.toBeNull();                                // still the form: nothing was started
    await type(field(dialog, "Wave size"), "3");                                                   // a change makes the preview stale
    expect(dialog.querySelector("[aria-label=Waves]")).toBeNull();
  });

  // An invalid form is stopped with a message and no call, and a refusal by the backend (a job already running) reaches the person as a message.
  it("says what is wrong before sending, and shows the refusal of the backend", async () => {
    const calls = bff("operator", report(), { "POST /smo/ran-nf-oam/software-campaigns": { status: 409, body: { title: "SERVICE_NAME_CONFLICT", detail: "a software job is already running on: ME-1" } } });
    const { dialog } = await start();
    await click(byText(dialog, "button", "Start")!);
    expect(dialog.querySelector("[role=alert]")?.textContent).toContain("needs a name");
    await type(field(dialog, "Name"), "x");
    await click(byText(dialog, "button", "Start")!);
    expect(dialog.querySelector("[role=alert]")?.textContent).toContain("at least one managed element");
    expect(posts(calls)).toHaveLength(0);
    const select = field<HTMLSelectElement>(dialog, "Managed elements");
    select.options[0].selected = true;
    await pick(select, "ME-1");
    await click(byText(dialog, "button", "Start")!);
    await settle();
    expect(posts(calls)).toHaveLength(1);
    expect(document.body.textContent).toContain("a software job is already running on: ME-1");     // the toast
  });
});

describe("one campaign", () => {
  const openDetail = async (role: "viewer" | "operator" | "admin", detail: Record<string, unknown> = report(), overrides: Record<string, unknown> = {}) => {
    const calls = bff(role, detail, overrides);
    const { container } = await open();
    await settle();
    await click(rowOf(container, "r3-upgrade"));
    await settle();
    return { calls, drawer: document.querySelector("[role=dialog]") as HTMLElement };
  };

  // The drawer of a halted campaign shows the reason and detail, the settings, the elements needing attention, each wave's jobs (a timed-out one marked, an unstarted wave waiting) and the events.
  it("shows the waves with each element's job, the totals, the settings and the event log", async () => {
    const { drawer } = await openDetail("viewer", report({
      status: "HALTED", haltedReason: "GATE_FAILED", haltedDetail: "1 software job(s) of wave 1 failed (first: ME-2, in phase INSTALL)", attention: [{ managedElementRef: "ME-2", problem: "software job timed out in phase INSTALL: the element did not report" }],
      waves: [{ wave: 1, elements: ["ME-1", "ME-2"], started: true, jobs: [job("ME-1"), job("ME-2", { status: "FAILED", phase: "INSTALL", timedOut: true })] }, { wave: 2, elements: ["ME-3", "ME-4"], started: false, jobs: [] }],
      summary: { elements: 4, started: 2, notReached: 2, completed: 1, failed: 1, inProgress: 0, reverted: 0 },
    }));
    expect(drawer.textContent).toContain("Campaign r3-upgrade");
    expect(drawer.textContent).toContain("A health gate failed");
    expect(drawer.textContent).toContain("1 software job(s) of wave 1 failed");
    expect(drawer.textContent).toContain("a job times out after 900 s");
    expect(drawer.textContent).toContain("rollback last wave first");
    expect(drawer.querySelector("ul[aria-label='Needs attention']")?.textContent).toContain("ME-2: software job timed out in phase INSTALL");
    const wave1 = Array.from(drawer.querySelectorAll("section")).find((s) => s.textContent?.startsWith("Wave 1"))!;
    expect(wave1.textContent).toContain("timed out");
    const wave2 = Array.from(drawer.querySelectorAll("section")).find((s) => s.textContent?.startsWith("Wave 2"))!;
    expect(wave2.textContent).toContain("(not started)");
    expect(wave2.textContent).toContain("waiting");
    expect(drawer.querySelector("ol[aria-label='Campaign events']")?.textContent).toContain("WAVE_STARTED (wave 1): 2 element(s)");
  });

  // A viewer sees the campaign but gets no action buttons, even after a failed gate.
  it("gives a viewer no action", async () => {
    const { drawer } = await openDetail("viewer", report({ status: "HALTED", haltedReason: "GATE_FAILED" }));
    expect(drawer.querySelector("[aria-label='Campaign actions']")).toBeNull();
  });

  // A running campaign offers only Halt, and halting posts an empty body because the backend records who halted it.
  it("offers halt to a running campaign and nothing else", async () => {
    const { calls, drawer } = await openDetail("operator", report(), { [`POST /smo/ran-nf-oam/software-campaigns/${CID}/halt`]: summary(CID, { status: "HALTED" }) });
    const buttons = Array.from(drawer.querySelectorAll("[aria-label='Campaign actions'] button")).map((b) => b.textContent);
    expect(buttons).toEqual(["Halt"]);
    await click(byText(drawer, "button", "Halt")!);
    await settle();
    expect(posts(calls)[0].path).toBe(`/smo/ran-nf-oam/software-campaigns/${CID}/halt`);
    expect(posts(calls)[0].body).toEqual({});                                                      // who halted it is the backend's to set
  });

  // After a failed gate the drawer offers continue, abort and roll back, asks before continuing anyway and before rolling back, and offers no pause to skip.
  it("after a failed gate offers continue (asking first), abort and roll back", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const { calls, drawer } = await openDetail("operator", report({ status: "HALTED", haltedReason: "GATE_FAILED" }), {
      [`POST /smo/ran-nf-oam/software-campaigns/${CID}/continue`]: summary(CID), [`POST /smo/ran-nf-oam/software-campaigns/${CID}/abort`]: summary(CID, { status: "ABORTED" }),
      [`POST /smo/ran-nf-oam/software-campaigns/${CID}/rollback`]: summary(CID, { status: "ROLLING_BACK" }),
    });
    const group = drawer.querySelector("[aria-label='Campaign actions']") as HTMLElement;
    expect(Array.from(group.querySelectorAll("button")).map((b) => b.textContent)).toEqual(["Continue", "Abort", "Roll back"]);
    expect(group.querySelector("input[type=checkbox]")).toBeNull();                                // no pause to skip after a failed gate
    await click(byText(group, "button", "Continue")!);
    await settle();
    expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining("gate of this wave failed"));
    await click(byText(group, "button", "Abort")!);
    await settle();
    await click(byText(group, "button", "Roll back")!);
    await settle();
    expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining("the last wave first"));
    expect(posts(calls).map((c) => c.path.split("/").pop())).toEqual(["continue", "abort", "rollback"]);
    expect(posts(calls)[0].body).toEqual({});
  });

  // Declining the confirmation of an action makes no call.
  it("does not run an action the person declines", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const { calls, drawer } = await openDetail("operator", report({ status: "HALTED", haltedReason: "OPERATOR_HALT" }));
    await click(byText(drawer, "button", "Abort")!);
    await settle();
    expect(posts(calls)).toHaveLength(0);
  });

  // A campaign held by its wave pause also offers Halt and a box to skip the pause: Continue sends an empty body unless it is ticked, then force true.
  it("lets an operator skip the pause between waves, and halt it", async () => {
    const { calls, drawer } = await openDetail("operator", report({ status: "HALTED", haltedReason: "WAVE_PAUSE", nextWaveAt: "2026-10-09T12:00:00Z" }), {
      [`POST /smo/ran-nf-oam/software-campaigns/${CID}/continue`]: summary(CID) });
    const group = drawer.querySelector("[aria-label='Campaign actions']") as HTMLElement;
    expect(Array.from(group.querySelectorAll("button")).map((b) => b.textContent)).toEqual(["Continue", "Halt", "Abort", "Roll back"]);
    expect(drawer.textContent).toContain("Continues by itself at");
    await click(byText(group, "button", "Continue")!);
    await settle();
    expect(posts(calls)[0].body).toEqual({});                                                      // not forced
    await click(group.querySelector("input[type=checkbox]") as HTMLElement);
    await click(byText(group, "button", "Continue")!);
    await settle();
    expect(posts(calls)[1].body).toEqual({ force: true });
  });

  // A failed rollback can be retried, and a rolled-back campaign offers no action.
  it("offers a rollback again after one failed, and nothing once it has been rolled back", async () => {
    const failed = await openDetail("operator", report({ status: "ROLLBACK_FAILED", rollbackOrder: "all" }));
    expect(Array.from(failed.drawer.querySelectorAll("[aria-label='Campaign actions'] button")).map((b) => b.textContent)).toEqual(["Roll back"]);
    cleanup();
    const done = await openDetail("operator", report({ status: "ROLLED_BACK" }));
    expect(done.drawer.querySelector("[aria-label='Campaign actions']")).toBeNull();
  });

  // A rolled-back campaign shows each job's revert result and lists the failed revert and the elements never reached as needing attention.
  it("shows the reverts of a rolled back campaign and the elements never reached", async () => {
    const { drawer } = await openDetail("viewer", report({
      status: "ROLLED_BACK", waves: [{ wave: 1, elements: ["ME-1", "ME-2"], started: true, jobs: [job("ME-1", { revert: "COMPLETED" }), job("ME-2", { revert: "FAILED" })] }, { wave: 2, elements: ["ME-3", "ME-4"], started: false, jobs: [] }],
      attention: [{ managedElementRef: "ME-2", problem: "the revert job failed" }, { managedElementRef: "ME-3", problem: "never reached: the campaign ended before its wave" }],
    }));
    expect(drawer.textContent).toContain("Every completed job has been reverted");
    const wave1 = Array.from(drawer.querySelectorAll("section")).find((s) => s.textContent?.startsWith("Wave 1"))!;
    expect(wave1.textContent).toContain("COMPLETED");
    expect(wave1.textContent).toContain("FAILED");
    expect(drawer.querySelector("ul[aria-label='Needs attention']")?.textContent).toContain("never reached");
  });

  // A report that cannot be read shows the backend's message in the drawer instead of an empty one.
  it("shows the error when the report cannot be read", async () => {
    const calls = bff("viewer", { status: 404, body: { title: "SOFTWARE_CAMPAIGN_NOT_FOUND", detail: "no software campaign" } } as never);
    void calls;
    const { container } = await open();
    await settle();
    await click(rowOf(container, "r3-upgrade"));
    await settle();
    expect(document.querySelector("[role=dialog]")?.textContent).toContain("no software campaign");
  });
});
