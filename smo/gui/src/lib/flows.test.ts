import { describe, expect, it } from "vitest";

import type { ConfigJob, Instance, Model, NfDeployment, O1Endpoint, Package, ServiceOrder } from "../api/types";
import { FLOWS, flow01, flow02, flow03, flow05, flow06, flow07, flow09, flow10, progress, settle, type FlowStep } from "./flows";

const statuses = (steps: FlowStep[]) => steps.map((s) => s.status);

const pkg = (state: string, extra: Partial<Package> = {}): Package => ({
  packageId: "p1", name: "hello", version: "1.0", vendor: null, applicationType: "rApp", state, toscaEntryDefinitions: null,
  signatureVerified: true, nfDeploymentDescriptorId: state === "AVAILABLE" ? "d1" : null, ...extra,
});
const instance = (state: string): Instance => ({ instanceId: "i1", packageId: "p1", state, workloadRef: "nf1", configuration: {}, pendingUpgradeInstanceId: null });
const model = (state: string, nodeGroups: string[] = []): Model => ({
  modelId: "m1", modelType: "ts", version: "1", state, clearedNodeGroups: nodeGroups, artifactLocation: null, description: null,
  author: null, owner: null, inputDataType: null, outputDataType: null, targetEnvironments: [],
});

describe("the ten documented flows", () => {
  it("are all tracked, in order", () => {
    expect(FLOWS.map((f) => f.id)).toEqual(["01", "02", "03", "04", "05", "06", "07", "08", "09", "10"]);
    expect(FLOWS.every((f) => f.doc.startsWith(f.id + "-") && f.doc.endsWith(".md"))).toBe(true);
  });
});

describe("settle", () => {
  it("marks the first open step current and blocks everything after a failure", () => {
    const raw: FlowStep[] = ["done", "todo", "todo"].map((s, i) => ({ id: `${i}`, title: "", actor: "", status: s as FlowStep["status"] }));
    expect(statuses(settle(raw))).toEqual(["done", "current", "todo"]);
    raw[1].status = "failed";
    expect(statuses(settle(raw))).toEqual(["done", "failed", "blocked"]);
  });
});

describe("flow 01 — rApp onboarding → running", () => {
  it("walks the package through to a running instance", () => {
    expect(statuses(flow01(undefined, undefined, undefined))).toEqual(["current"]);
    expect(statuses(flow01(pkg("ONBOARDING"), undefined, undefined))).toEqual(["done", "current", "todo", "todo", "todo", "todo"]);
    const dep = { nfDeploymentId: "nf1", name: "x", state: "RUNNING", clusterId: "c", nfDeploymentDescriptorId: "d1", workloadRef: null, requiredResourceTypeId: null } as NfDeployment;
    expect(statuses(flow01(pkg("AVAILABLE"), instance("DEPLOYING"), dep))).toEqual(["done", "done", "done", "done", "done", "current"]);
    expect(progress(flow01(pkg("AVAILABLE"), instance("RUNNING"), dep)).complete).toBe(true);
  });

  it("stops at a validation failure", () => {
    const steps = flow01(pkg("FAILED"), undefined, undefined);
    expect(statuses(steps)).toEqual(["done", "failed", "blocked", "blocked", "blocked", "blocked"]);
    expect(progress(steps).failed).toBe(true);
  });
});

describe("flow 02 — AI/ML model", () => {
  it("follows the model FSM", () => {
    const at = (state: string, groups: string[] = []) => statuses(flow02(model(state, groups), [], [], [], []));
    expect(at("REGISTERED").slice(0, 3)).toEqual(["done", "current", "todo"]);
    expect(at("CERTIFIED").slice(0, 6)).toEqual(["done", "done", "done", "done", "done", "current"]);
    expect(at("ACTIVE", ["edge-a"]).slice(0, 9)).toEqual(["done", "done", "done", "done", "done", "done", "done", "done", "current"]);
  });

  it("flags a floor breach as a warning, not a failure", () => {
    const steps = flow02(model("ACTIVE", ["g"]), [], [{ inferenceJobId: "j", modelId: "m1", status: "COMPLETED", notificationDestination: null }],
      [{ subscriptionId: "s", modelId: "m1", metricTypes: ["acc"], dmeTypeId: "t", guardKpiFloor: { acc: 0.9 } }],
      [{ reportId: "r", subscriptionId: "s", metrics: { acc: 0.5 }, breachedFloor: true, reportedAt: "2026-01-01T00:00:00Z" }]);
    expect(steps.at(-1)?.status).toBe("warn");
    expect(progress(steps).complete).toBe(true);
  });
});

describe("flow 03 — config write", () => {
  const ep = (health: string): O1Endpoint => ({ endpointId: "e", managedElementRef: "ME-1", adaptorUri: "u", protocolSupport: ["NETCONF"], registeredVia: "x", healthStatus: health, lastHeartbeatAt: null });
  it("reports PARTIAL_SUCCESS as a warning", () => {
    const job: ConfigJob = { jobId: "j", status: "PARTIAL_SUCCESS", subChanges: [
      { managedElementRef: "ME-1", operation: "merge", status: "APPLIED", rejectionReason: null },
      { managedElementRef: "ME-2", operation: "merge", status: "REJECTED", rejectionReason: "ENDPOINT_UNREACHABLE" }] };
    const s = flow03([ep("ACTIVE")], job);
    expect(statuses(s)).toEqual(["done", "done", "done", "done", "warn", "warn"]);
  });
  it("wants a heartbeat when no endpoint is ACTIVE", () => {
    expect(flow03([ep("DISCOVERED")], undefined)[1].status).toBe("warn");
  });
});

describe("flow 05 — A1 EI → consumption", () => {
  it("progresses from registration to an active consumer job", () => {
    const ei = { eiTypeId: "ei", registeredBy: "rapp", eiSourceDmeTypeId: "t" };
    expect(statuses(flow05(ei, undefined, [], []))).toEqual(["done", "current", "todo", "todo", "todo", "todo"]);
    const done = flow05(ei, { dmeTypeId: "t", dmeTypeIdStruct: {}, typeName: "RAN.X", producerId: "p", typeStatus: "ENABLED" },
      [{ offerId: "o", dmeTypeId: "t", dataDeliveryMethodsOffered: ["PULL_HTTP"], committedMethod: "PULL_HTTP", dataAvailabilityNotificationUri: null, dataOfferTerminationNotificationUri: "x" }],
      [{ dataJobId: "j", dataDeliveryMode: "CONTINUOUS", dmeTypeId: "t", productionJobDefinition: {}, dataDeliveryMethod: "PULL_HTTP", deliveryDetails: {}, consumerId: "c", status: "ACTIVE" }]);
    expect(progress(done).complete).toBe(true);
  });
});

describe("flow 06 — the cascade-delete guard", () => {
  it("shows active usage as what blocks delete", () => {
    const steps = flow06(pkg("DEPRECATED"), [{ registrationId: "r", consumerId: "i1", stoppedAt: null, active: true }], 1);
    expect(steps[3].status).toBe("failed");
    expect(steps[3].detail).toContain("delete is blocked");
    expect(steps[4].status).toBe("blocked");
  });
  it("a FAILED package skips straight to delete", () => {
    expect(statuses(flow06(pkg("FAILED"), [], 0))).toEqual(["warn", "done", "done", "done", "current"]);
  });
});

describe("flow 07 — fault reporting", () => {
  it("a critical fault leaves the instance FAULTED until recovered", () => {
    const crit = [{ faultId: "f", severity: "critical", description: null, reportedAt: "t" }];
    const perf = [{ reportId: "r", metrics: { x: 1 }, reportedAt: "t" }];
    const faults = [...crit, { faultId: "m", severity: "minor", description: null, reportedAt: "t" }];
    const faulted = flow07(instance("FAULTED"), perf, faults);
    expect(faulted[3].status).toBe("warn");
    expect(faulted[4].status).toBe("current");   // RECOVER stays actionable
    expect(flow07(instance("RUNNING"), perf, faults)[4].status).toBe("done");
  });
});

describe("flow 09 — intents", () => {
  it("warns when an intent has no handler to receive it", () => {
    const intent = { intentId: "i", intentAdminState: "ACTIVATED", intentPriority: 1, rmioId: "smo-gui", intentMgmtPurpose: null };
    expect(flow09([], intent, [])[2].status).toBe("warn");
  });
});

describe("flow 10 — SO multi-step", () => {
  it("mirrors fail-fast: steps after a failure are blocked", () => {
    const order: ServiceOrder = { orderId: "o", scope: "s", homingDecision: null, rmihRegistration: "so-smos", steps: [
      { stepType: "INFRA", targetModule: "FOCOM", status: "COMPLETED" },
      { stepType: "TRAINING", targetModule: "AI_ML_WORKFLOW", status: "FAILED", error: "422" },
      { stepType: "DEPLOY", targetModule: "NFO", status: "PENDING" }] };
    expect(statuses(flow10(order))).toEqual(["done", "done", "failed", "blocked"]);
  });
});
