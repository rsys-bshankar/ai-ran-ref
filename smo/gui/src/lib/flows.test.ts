/**
 * Unit tests of the journey evaluators in `flows.ts`: the list of tracked flows, `settle`, and the step statuses of each flow for representative entity states. No DOM.
 * Run: `cd gui && npx vitest run src/lib/flows.test.ts`.
 */

import { describe, expect, it } from "vitest";

import type { ConfigJob, Instance, Model, ModelLifecycle, NfDeployment, O1Endpoint, Package, ServiceOrder } from "../api/types";
import { FLOWS, flow01, flow02, flow03, flow06, flow07, flow09, flow10, progress, settle, type FlowStep } from "./flows";

const statuses = (steps: FlowStep[]) => steps.map((s) => s.status);

const pkg = (state: string, extra: Partial<Package> = {}): Package => ({
  packageId: "p1", name: "EnergySaving_rApp", version: "1.0", vendor: null, applicationType: "rApp", state, toscaEntryDefinitions: null,
  signatureVerified: true, nfDeploymentDescriptorId: state === "AVAILABLE" ? "d1" : null, aiCapabilities: null,
  descriptorId: null, descriptorInvariantId: null, descriptorVersion: null, schemaVersion: null, smeDeclarations: null, ...extra,
});
const instance = (state: string): Instance => ({
  instanceId: "i1", packageId: "p1", state, workloadRef: "nf1", configuration: {}, pendingUpgradeInstanceId: null, smeServiceIds: null,
  autonomyMode: "SHADOW", regionScope: null,
});
const model = (): Model => ({
  modelId: "m1", modelType: "ts", version: "1", artifactLocation: null, description: null,
  author: null, owner: null, inputDataType: null, outputDataType: null, targetEnvironments: [],
});
const lifecycle = (modelLifecycleState: string, runtimeLifecycleState = "NOT_DEPLOYED", nodeGroups: string[] = []): ModelLifecycle => ({
  modelId: "m1", modelLifecycleState, runtimeLifecycleState, trainingJobId: null,
  clearedNodeGroups: nodeGroups, nfDeploymentDescriptorId: null, nfDeploymentId: null,
  trainingApproved: false, validationApproved: false,
});

describe("the ten documented flows", () => {
  // The tracked flows are those of the call-flow documents (01 to 10 without 05), in order, and each points at the document that starts with its number.
  it("are all tracked, in order", () => {
    expect(FLOWS.map((f) => f.id)).toEqual(["01", "02", "03", "04", "06", "07", "08", "09", "10"]);
    expect(FLOWS.every((f) => f.doc.startsWith(f.id + "-") && f.doc.endsWith(".md"))).toBe(true);
  });
});

describe("settle", () => {
  // `settle` marks the first open step current and, after a failure, blocks every step still to do.
  it("marks the first open step current and blocks everything after a failure", () => {
    const raw: FlowStep[] = ["done", "todo", "todo"].map((s, i) => ({ id: `${i}`, title: "", actor: "", status: s as FlowStep["status"] }));
    expect(statuses(settle(raw))).toEqual(["done", "current", "todo"]);
    raw[1].status = "failed";
    expect(statuses(settle(raw))).toEqual(["done", "failed", "blocked"]);
  });
});

describe("flow 01 — rApp onboarding → running", () => {
  // Flow 01 advances step by step from the package to a running instance, and is complete only at the end.
  it("walks the package through to a running instance", () => {
    expect(statuses(flow01(undefined, undefined, undefined))).toEqual(["current"]);
    expect(statuses(flow01(pkg("ONBOARDING"), undefined, undefined))).toEqual(["done", "current", "todo", "todo", "todo", "todo"]);
    const dep = { nfDeploymentId: "nf1", name: "x", state: "RUNNING", clusterId: "c", nfDeploymentDescriptorId: "d1", workloadRef: null, requiredResourceTypeId: null } as NfDeployment;
    expect(statuses(flow01(pkg("AVAILABLE"), instance("DEPLOYING"), dep))).toEqual(["done", "done", "done", "done", "done", "current"]);
    expect(progress(flow01(pkg("AVAILABLE"), instance("RUNNING"), dep)).complete).toBe(true);
  });

  // A package that failed validation fails that step and blocks all that follow.
  it("stops at a validation failure", () => {
    const steps = flow01(pkg("FAILED"), undefined, undefined);
    expect(statuses(steps)).toEqual(["done", "failed", "blocked", "blocked", "blocked", "blocked"]);
    expect(progress(steps).failed).toBe(true);
  });
});

describe("flow 02 — AI/ML model", () => {
  // Flow 02 follows the model lifecycle and the runtime lifecycle: each state makes the steps before it done and the next one current.
  it("follows the model FSM", () => {
    const at = (state: string, runtimeState = "NOT_DEPLOYED", groups: string[] = []) =>
      statuses(flow02(model(), lifecycle(state, runtimeState, groups), [], [], [], []));
    expect(at("REGISTERED").slice(0, 3)).toEqual(["done", "current", "todo"]);
    expect(at("CERTIFIED").slice(0, 6)).toEqual(["done", "done", "done", "done", "done", "current"]);
    expect(at("PROMOTED", "ACTIVE", ["edge-a"]).slice(0, 9)).toEqual(["done", "done", "done", "done", "done", "done", "done", "done", "current"]);
  });

  // A performance report under the guard floor is a warning (a retrain follows), not a failure, so the flow can still complete.
  it("flags a floor breach as a warning, not a failure", () => {
    const steps = flow02(model(), lifecycle("PROMOTED", "ACTIVE", ["g"]), [], [{ inferenceJobId: "j", modelId: "m1", status: "COMPLETED", notificationDestination: null, nfDeploymentId: null }],
      [{ subscriptionId: "s", modelId: "m1", metricTypes: ["acc"], dmeTypeId: "t", guardKpiFloor: { acc: 0.9 }, notificationDestination: null }],
      [{ reportId: "r", subscriptionId: "s", metrics: { acc: 0.5 }, breachedFloor: true, reportedAt: "2026-01-01T00:00:00Z" }]);
    expect(steps.at(-1)?.status).toBe("warn");
    expect(progress(steps).complete).toBe(true);
  });
});

describe("flow 03 — config write", () => {
  const ep = (health: string): O1Endpoint => ({ endpointId: "e", managedElementRef: "ME-1", adaptorUri: "u", protocolSupport: ["NETCONF"], registeredVia: "x", healthStatus: health, lastHeartbeatAt: null });
  // A job with some applied and some rejected changes ends in warnings, not in a failure.
  it("reports PARTIAL_SUCCESS as a warning", () => {
    const job: ConfigJob = { jobId: "j", status: "PARTIAL_SUCCESS", subChanges: [
      { managedElementRef: "ME-1", operation: "merge", status: "APPLIED", rejectionReason: null },
      { managedElementRef: "ME-2", operation: "merge", status: "REJECTED", rejectionReason: "ENDPOINT_UNREACHABLE" }] };
    const s = flow03([ep("ACTIVE")], job);
    expect(statuses(s)).toEqual(["done", "done", "done", "done", "warn", "warn"]);
  });
  // While no endpoint is ACTIVE the health step is a warning that asks for a heartbeat.
  it("wants a heartbeat when no endpoint is ACTIVE", () => {
    expect(flow03([ep("DISCOVERED")], undefined)[1].status).toBe("warn");
  });
});

describe("flow 06 — the cascade-delete guard", () => {
  // Active usage is shown as the failing guard step, so the delete step reads as blocked.
  it("shows active usage as what blocks delete", () => {
    const steps = flow06(pkg("DEPRECATED"), [{ registrationId: "r", consumerId: "i1", stoppedAt: null, active: true }], 1);
    expect(steps[4].status).toBe("failed");
    expect(steps[4].detail).toContain("delete is blocked");
    expect(steps[5].status).toBe("blocked");
  });
  // A package that failed onboarding skips priming and deprecation and goes straight to delete.
  it("a FAILED package skips straight to delete", () => {
    expect(statuses(flow06(pkg("FAILED"), [], 0))).toEqual(["warn", "done", "done", "done", "done", "current"]);
  });
  // Priming is optional, and a primed package is told to deprime before it is deprecated.
  it("priming is optional, and a primed package must be deprimed before it is deprecated", () => {
    expect(flow06(pkg("AVAILABLE"), [], 0)[1].detail).toBe("optional — not primed");
    const primed = flow06(pkg("PRIMED"), [], 1);
    expect(primed[1].detail).toBe("PRIMED");
    expect(primed[3]).toMatchObject({ status: "current", detail: "deprime first (refused while usage is active)" });
  });
});

describe("flow 07 — fault reporting", () => {
  // A critical fault leaves the instance FAULTED as a warning (not a dead end), so RECOVER stays the actionable step.
  it("a critical fault leaves the instance FAULTED until recovered", () => {
    const crit = [{ faultId: "f", severity: "critical", description: null, reportedAt: "t" }];
    const perf = [{ reportId: "r", metrics: { x: 1 }, reportedAt: "t" }];
    const faults = [...crit, { faultId: "m", severity: "minor", description: null, reportedAt: "t" }];
    const faulted = flow07(instance("FAULTED"), perf, faults);
    expect(faulted[3].status).toBe("warn");
    expect(faulted[4].status).toBe("current");   // RECOVER stays actionable
    expect(flow07(instance("RUNNING"), perf, faults)[4].status).toBe("done");
  });
  // An upgrade in progress waits for resolve, naming the replacement instance, and terminating completes the journey.
  it("an upgrade awaits resolve, and terminate ends the journey", () => {
    const upgrading = flow07({ ...instance("UPGRADING"), pendingUpgradeInstanceId: "new-1" }, [], []);
    expect(upgrading[5]).toMatchObject({ status: "warn", detail: "awaiting upgrade/resolve (replacement new-1)" });
    expect(progress(flow07(instance("UNDEPLOYED"), [], [])).complete).toBe(true);
  });
});

describe("flow 09 — intents", () => {
  // Once an intent exists the dispatch to the named handler counts as done, because the consumer chose a valid target when it created the intent.
  it("marks the named-RMIH dispatch step done once an intent exists (consumer-side selection guarantees a valid target)", () => {
    const intent = { intentId: "i", intentAdminState: "ACTIVATED", intentPriority: 1, rmioId: "smo-gui", intentMgmtPurpose: null, rmihId: "so-smos", userLabel: "t", attributes: {} };
    expect(flow09([], intent, [])[2].status).toBe("done");
  });
  // Intent Service's own first RECEIVED report does not count as the handler having reported.
  it("counts only handler reports, not Intent Service's own initial RECEIVED report", () => {
    const intent = { intentId: "i", intentAdminState: "ACTIVATED", intentPriority: 1, rmioId: "smo-gui", intentMgmtPurpose: null, rmihId: "so-smos", userLabel: "t", attributes: {} };
    const report = (n: number) => ({ reportId: `r${n}`, intentId: "i", attributes: { lastUpdatedTime: "2026-01-01T00:00:00Z" } });
    expect(flow09([], intent, [report(1)])[3].status).not.toBe("done");
    expect(flow09([], intent, [report(1), report(2)])[3].status).toBe("done");
  });
});

describe("flow 10 — SO multi-step", () => {
  // An order step that fails blocks the later ones, as the order itself stops there without compensation.
  it("mirrors fail-fast: steps after a failure are blocked", () => {
    const order: ServiceOrder = { orderId: "o", scope: "s", homingDecision: null, rmihRegistration: "so-smos", steps: [
      { stepType: "INFRA", targetModule: "FOCOM", status: "COMPLETED" },
      { stepType: "TRAINING", targetModule: "AI_ML_WORKFLOW", status: "FAILED", error: "422" },
      { stepType: "DEPLOY", targetModule: "NFO", status: "PENDING" }] };
    expect(statuses(flow10(order))).toEqual(["done", "done", "failed", "blocked"]);
  });
});
