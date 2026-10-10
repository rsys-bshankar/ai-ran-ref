// @vitest-environment jsdom
/** Tests of the Configuration page (pages/configuration) against a fake BFF: the job list shows halted jobs first, a staged job's detail
 * shows its waves, pause and controls only to a role that may use them, the tabs (vendors and schemas, endpoint trust, element onboarding)
 * render their server lists, onboarding's Apply is role-gated and posts, a new job dry-runs, and the wave-state rule. Run:
 * `npx vitest run src/pages/configuration`. */
import { act } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ConfigJob } from "../../../api/types";
import { AuthProvider } from "../../../auth/AuthContext";
import rules from "../../../auth/permissions.fixture.json";
import { fakeBff, mountWith, type Call } from "../../../testing/bff";
import { byText, cleanup, click, settle, type } from "../../../testing/dom";
import { jobWaveState } from "../data/types";
import { Configuration } from "../index";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
beforeEach(() => { document.body.innerHTML = ""; window.location.hash = ""; });

const J1 = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee";
const later = () => new Date(Date.now() + 372_000).toISOString();
const JOB: ConfigJob = {
  jobId: J1, status: "HALTED", requestedBy: "rapp-coverage", waveSize: 1, waveCount: 3, currentWave: 1, wavePauseSeconds: 600, onGateFailure: "revert", gateMaxNewAlarms: 0,
  haltedReason: "WAVE_PAUSE", haltedDetail: null, nextWaveAt: later(),
  kpiGuard: { kpi: "dlThroughputP10", baselineMinutes: 30, observationMinutes: 15, maxRegressionPercent: 5, direction: "higher", minSamples: 1, revert: true, msacRole: null },
  kpiGuardResult: null, kpiGuardCheckedAt: null,
  subChanges: [
    { managedElementRef: "du-1", wave: 1, operation: "merge", status: "APPLIED", rejectionReason: null },
    { managedElementRef: "du-2", wave: 2, operation: "merge", status: "PENDING", rejectionReason: null },
    { managedElementRef: "du-3", wave: 3, operation: "merge", status: "PENDING", rejectionReason: null },
  ],
};

/** A fake BFF for the page (bodies with a `status` field are wrapped in `{ body }`). */
function bff(role: "viewer" | "operator" | "admin") {
  return fakeBff({
    "GET /me": { username: "ana", role, csrfToken: "c", local: true, totpEnrolled: true, mfaEnrolmentRequired: false },
    "GET /permissions": { role, rules },
    "GET /summary/configuration": { page: "configuration", computedAt: "", partial: [], counts: { "configJobs.HALTED": 2, "configJobs.PROCESSING": 1, "configJobs.PENDING": 0, "configJobs.total": 40 } },
    "GET /smo/*": (c: Call) => {
      const p = c.path.replace("/smo/ran-nf-oam", "");
      if (p === "/config-jobs") return { items: [{ jobId: J1, requestedBy: "rapp-coverage", accessScope: "cluster", scope: "cluster", status: "HALTED", msacRole: null }], total: 1, limit: 25, offset: 0 };
      if (p === `/config-jobs/${J1}`) return { body: JOB };
      if (p === "/decision-records") return { items: [], limit: 1, offset: 0 };
      if (p === "/kpi-definitions") return { items: [{ name: "dlThroughputP10" }], limit: 100, offset: 0 };
      if (p === "/vendor-capabilities") return { items: [{ vendorName: "vendor-b", supportedServices: ["PROV", "FM"], conformanceMode: "COMBINED", supportedVendorModes: ["O1_NETCONF"], schemaRef: { schemaName: "vendor-b-nrm", revision: "2.4" }, specSchemaRef: { schemaName: "3gpp-ts28541-nrnrm", revision: "19.6.0" }, discoveryUri: null, updatedAt: "2026-10-01T00:00:00Z" }], total: 1, limit: 25, offset: 0 };
      if (p === "/cm-schemas") return { items: [{ schemaName: "3gpp-ts28541-nrnrm", revision: "19.6.0", type: "DESCRIPTOR", location: "builtin:x.json", builtin: true, classCount: 12 }], total: 1, limit: 25, offset: 0 };
      if (p === "/o1-adaptor-endpoints") return { items: [{ endpointId: "ep-1", managedElementRef: "du-1", adaptorUri: "ssh://o1@h2", transport: "ssh", healthStatus: "ACTIVE", lastHeartbeatAt: null }], total: 1, limit: 25, offset: 0 };
      if (p === "/o1-adaptor-endpoints/ep-1/host-keys") return { items: [] };
      if (p === "/element-onboarding") return { items: [{ managedElementRef: "du-9", status: "TEMPLATE_SELECTED", templateName: "du-std-v3", softwareVersion: "24.3.1", softwareBaseline: "24.3.1", softwareCheck: "MATCH", configJobId: null, detail: null, createdAt: null, updatedAt: null }], total: 1, limit: 25, offset: 0 };
      return { status: 404, body: { title: "NOT_FOUND" } };
    },
    "POST /smo/*": (c: Call) => ((c.body as { dryRun?: boolean })?.dryRun
      ? { body: { dryRun: true, status: "WOULD_REJECT_SOME", waves: [["du-1"], ["du-2"]], changes: [{ managedElementRef: "du-1", managedFunctionRef: null, operation: "merge", verdict: "PASS", reason: null }, { managedElementRef: "du-2", managedFunctionRef: null, operation: "merge", verdict: "WOULD_REJECT", reason: "ENDPOINT_UNREACHABLE" }] } }
      : { status: 202, body: { jobId: J1, status: "PROCESSING" } }),
  });
}

/** Types into a textarea the way `testing/dom.type` does for an input. */
async function typeArea(area: HTMLTextAreaElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
  await act(async () => { setter.call(area, value); area.dispatchEvent(new Event("input", { bubbles: true })); });
}

/** Mounts the page as `role` at `at` and waits for its first reads; returns the mount and the recorded calls. */
const open = async (role: "viewer" | "operator" | "admin", at = "/configuration") => {
  const calls = bff(role);
  const m = await mountWith(<AuthProvider><Configuration /></AuthProvider>, { at });
  await settle();
  return { ...m, calls };
};

describe("Configuration page", () => {
  // With halted jobs in the summary, the list asks the server for the halted ones first.
  it("lists config jobs with the halted ones first", async () => {
    const { container, calls } = await open("viewer");
    expect(calls.some((c) => c.path === "/smo/ran-nf-oam/config-jobs" && c.query.get("status") === "HALTED")).toBe(true);
    expect(container.querySelector("[data-section='configuration.jobs']")!.textContent).toContain("rapp-coverage");
    expect(container.querySelector("[data-section='configuration.tiles']")!.textContent).toContain("waiting for an operator");
  });

  // A paused job shows its waves, the countdown and its KPI guard; only an operator gets Continue now, which posts with force.
  it("shows a staged job's waves and pause, with controls gated by role", async () => {
    const viewer = await open("viewer", `/configuration?job=${J1}`);
    const detail = viewer.container.querySelector("[data-section='configuration.job']")!;
    expect(detail.querySelectorAll(".cfg-wave").length).toBe(3);
    expect(detail.querySelector(".cfg-wave.applied")).not.toBeNull();
    expect(detail.textContent).toContain("WAVE_PAUSE · Waiting between waves");
    expect(detail.textContent).toMatch(/Wave 2 starts in 0\d:\d\d/);
    expect(detail.textContent).toContain("dlThroughputP10");
    expect(byText(detail, "button", "Continue now")).toBeNull();
    cleanup();
    vi.stubGlobal("confirm", () => true);
    const op = await open("operator", `/configuration?job=${J1}`);
    await click(byText(op.container, "button", "Continue now")!);
    await settle();
    const post = op.calls.find((c) => c.method === "POST")!;
    expect(post.path).toBe(`/smo/ran-nf-oam/config-jobs/${J1}/continue`);
    expect(post.body).toEqual({ force: true });
  });

  // The Vendors & schemas tab renders both server lists.
  it("renders vendor capabilities and CM schemas", async () => {
    window.location.hash = "#vendors";
    const { container } = await open("viewer");
    expect(container.querySelector("[data-section='configuration.vendors']")!.textContent).toContain("COMBINED");
    expect(container.querySelector("[data-section='configuration.vendors']")!.textContent).toContain("vendor-b-nrm@2.4 + 3gpp-ts28541-nrnrm@19.6.0");
    expect(container.querySelector("[data-section='configuration.schemas']")!.textContent).toContain("DESCRIPTOR");
  });

  // An ssh endpoint with no pinned key is a red alert: its connections are refused.
  it("alerts on an ssh endpoint with no pinned host key", async () => {
    window.location.hash = "#trust";
    const { container } = await open("viewer");
    await click(byText(container, "td", "ssh")!);
    await settle();
    expect(container.textContent).toContain("No host key pinned");
  });

  // Apply shows only to an operator, asks first, and posts to the element's apply route.
  it("gates the onboarding Apply by role", async () => {
    window.location.hash = "#onboarding";
    const viewer = await open("viewer");
    expect(viewer.container.textContent).toContain("du-std-v3");
    expect(byText(viewer.container, "button", "Apply")).toBeNull();
    cleanup();
    window.location.hash = "#onboarding";
    vi.stubGlobal("confirm", () => true);
    const op = await open("operator");
    await click(byText(op.container, "button", "Apply")!);
    await settle();
    expect(op.calls.find((c) => c.method === "POST")!.path).toBe("/smo/ran-nf-oam/element-onboarding/du-9/apply");
  });

  // The dry run sends every change with dryRun and lists what would be rejected.
  it("dry-runs a new config job", async () => {
    window.location.hash = "#new";
    const { container, calls } = await open("operator");
    const form = container.querySelector("[data-section='configuration.new']")!;
    const area = form.querySelector("textarea") as HTMLTextAreaElement;
    await typeArea(area, "du-1\ndu-2");
    await type(form.querySelectorAll("input")[1] as HTMLInputElement, "1");
    await settle(1);
    await click(byText(form, "button", "Dry run")!);
    await settle();
    expect(calls.find((c) => c.method === "POST")!.body).toMatchObject({ dryRun: true, accessScope: "cell", waveSize: 1, changes: [{ managedElementRef: "du-1" }, { managedElementRef: "du-2" }] });
    expect(form.textContent).toContain("ENDPOINT_UNREACHABLE");
  });
});

describe("wave state", () => {
  // A wave's state comes from its sub-changes; the next wave of a running job is "running".
  it("derives each wave's state from its sub-changes", () => {
    expect(jobWaveState(JOB, 1)).toBe("applied");
    expect(jobWaveState(JOB, 2)).toBe("waiting");
    expect(jobWaveState({ ...JOB, status: "PROCESSING" }, 2)).toBe("running");
    expect(jobWaveState({ ...JOB, subChanges: [{ ...JOB.subChanges[2], status: "REJECTED", rejectionReason: "WAVE_NOT_RUN" }] }, 3)).toBe("skipped");
  });
});
