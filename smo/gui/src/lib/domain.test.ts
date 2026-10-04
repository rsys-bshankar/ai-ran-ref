import { describe, expect, it } from "vitest";

import { completionRoute, countBySeverity, describeLimits, limitsForm, limitsPayload, keepAliveRemaining, metricSeries, modelActions, numericMetricKeys, packageActions, parseJsonObject, pipelineSteps, sortAlarms, splitList } from "./domain";

describe("model lifecycle", () => {
  it("maps each state to the FSM's next legal action", () => {
    expect(modelActions("REGISTERED")).toEqual([{ kind: "train", label: "Request training" }]);
    // job-driven completions go through the job's own /complete route, never advance
    expect(modelActions("TRAINING")).toEqual([{ kind: "complete", stage: "training", label: "Training complete" }]);
    expect(modelActions("VALIDATING").map((a) => a.kind === "complete" && a.stage)).toEqual(["validation"]);
    expect(modelActions("EMULATING").map((a) => a.kind === "complete" && a.stage)).toEqual(["emulation"]);
    expect(modelActions("CERTIFIED").map((a) => (a.kind === "advance" ? a.event : a.kind))).toEqual(["PROMOTE", "train", "DEPRECATE"]);
    expect(modelActions("EMULATED").map((a) => a.kind === "advance" && a.event)).toEqual(["SUBMIT_FOR_APPROVAL"]);
    expect(modelActions("PROMOTED").map((a) => (a.kind === "advance" ? a.event : a.kind))).toEqual(["train", "ROLLBACK", "DEPRECATE"]);
    expect(modelActions("DEPRECATED").map((a) => (a.kind === "advance" ? a.event : a.kind))).toEqual(["RETIRE"]);
  });

  it("routes each completion to its job's /complete route", () => {
    expect(completionRoute("training")).toEqual({ jobsPath: "/aimgf/training-jobs", runningStatus: "IN_PROGRESS", idKey: "trainingJobId" });
    expect(completionRoute("validation").jobsPath).toBe("/aimgf/validation-jobs");
    expect(completionRoute("emulation").idKey).toBe("emulationJobId");
  });

  it("marks stepper progress", () => {
    const steps = pipelineSteps("CERTIFIED");
    expect(steps.filter((s) => s.status === "done").map((s) => s.state)).toEqual([
      "REGISTERED", "TRAINING", "TRAINED", "VALIDATING", "VALIDATED", "EMULATING", "EMULATED", "PENDING_APPROVAL", "APPROVED",
    ]);
    expect(steps.find((s) => s.status === "current")?.state).toBe("CERTIFIED");
    expect(pipelineSteps("DEPRECATED").every((s) => s.status === "done")).toBe(true);
    expect(pipelineSteps("RETIRED").every((s) => s.status === "done")).toBe(true);
    expect(pipelineSteps("FAILED").every((s) => s.status === "done")).toBe(true);
  });
});

describe("package lifecycle", () => {
  it("offers only the onboarding FSM's legal calls", () => {
    expect(packageActions("AVAILABLE").map((a) => a.action)).toEqual(["prime", "deprecate", "delete"]);
    expect(packageActions("PRIMED").map((a) => a.action)).toEqual(["deprime"]);
    expect(packageActions("DEPRECATED").map((a) => a.action)).toEqual(["cancel-delete", "delete"]);
    expect(packageActions("ONBOARDING")).toEqual([]);
  });
});

describe("alarms", () => {
  const alarms = [
    { severity: "minor", raisedAt: "2026-01-01T00:00:00Z" },
    { severity: "critical", raisedAt: "2026-01-01T00:00:00Z" },
    { severity: "cleared", raisedAt: "2026-01-03T00:00:00Z" },
    { severity: "critical", raisedAt: "2026-01-02T00:00:00Z" },
  ];
  it("counts open severities only", () => {
    expect(countBySeverity(alarms)).toEqual({ critical: 2, major: 0, minor: 1, warning: 0 });
  });
  it("sorts most severe first, then newest", () => {
    expect(sortAlarms(alarms).map((a) => `${a.severity}@${a.raisedAt.slice(8, 10)}`)).toEqual(["critical@02", "critical@01", "minor@01", "cleared@03"]);
  });
});

describe("KPI series", () => {
  const reports = [
    { metrics: { accuracy: 0.8, note: "x" }, reportedAt: "2026-01-02T00:00:00Z" },
    { metrics: { accuracy: 0.9, latency: 5 }, reportedAt: "2026-01-01T00:00:00Z" },
  ];
  it("finds numeric keys and orders points oldest first", () => {
    expect(numericMetricKeys(reports)).toEqual(["accuracy", "latency"]);
    expect(metricSeries(reports, "accuracy").map((p) => p.v)).toEqual([0.9, 0.8]);
    expect(metricSeries(reports, "latency")).toHaveLength(1);
  });
});

describe("form helpers", () => {
  it("parses JSON objects only", () => {
    expect(parseJsonObject('{"a":1}')).toEqual({ ok: true, value: { a: 1 } });
    expect(parseJsonObject("")).toEqual({ ok: true, value: {} });
    expect(parseJsonObject("[1]").ok).toBe(false);
    expect(parseJsonObject("{").ok).toBe(false);
  });
  it("splits comma/newline lists", () => {
    expect(splitList(" a, b\nc ,, ")).toEqual(["a", "b", "c"]);
  });
});

describe("keepAliveRemaining", () => {
  it("is null for an unsupervised service", () => expect(keepAliveRemaining({ keepAliveIntervalSeconds: 0, timeSinceLastActivitySeconds: 99 })).toBeNull());
  it("counts down from the interval", () => expect(keepAliveRemaining({ keepAliveIntervalSeconds: 30, timeSinceLastActivitySeconds: 12 })).toBe(18));
  it("floors at zero once lapsed", () => expect(keepAliveRemaining({ keepAliveIntervalSeconds: 5, timeSinceLastActivitySeconds: 9 })).toBe(0));
});

describe("rApp limits", () => {
  it("describes what is in force, with the hourly use", () => {
    expect(describeLimits({ maxConfigJobsPerHour: 20, maxElementsPerJob: 5, maxChangePercent: 10, configJobsLastHour: 3 })).toBe("20/h (3 used) · ≤5 elements · ≤10%");
    expect(describeLimits({ maxConfigJobsPerHour: null, maxElementsPerJob: 5, maxChangePercent: null })).toBe("≤5 elements");
    expect(describeLimits(null)).toBe("No limits");
    expect(describeLimits({ maxConfigJobsPerHour: null, maxElementsPerJob: null, maxChangePercent: null })).toBe("No limits");
  });

  it("builds the PUT body from the form and leaves a blank field out", () => {
    expect(limitsPayload({ jobsPerHour: "20", elementsPerJob: "", changePercent: "12.5" })).toEqual({ ok: true, body: { maxConfigJobsPerHour: 20, maxChangePercent: 12.5 } });
    expect(limitsPayload({ jobsPerHour: " 7 ", elementsPerJob: "3", changePercent: "" })).toEqual({ ok: true, body: { maxConfigJobsPerHour: 7, maxElementsPerJob: 3 } });
  });

  it("refuses what RAN NF OAM would refuse, before sending it", () => {
    const bad = (f: Partial<{ jobsPerHour: string; elementsPerJob: string; changePercent: string }>) => limitsPayload({ jobsPerHour: "", elementsPerJob: "", changePercent: "", ...f });
    expect(bad({})).toMatchObject({ ok: false, error: expect.stringContaining("at least one") });
    expect(bad({ jobsPerHour: "0" })).toMatchObject({ ok: false });
    expect(bad({ jobsPerHour: "100001" })).toMatchObject({ ok: false });
    expect(bad({ jobsPerHour: "2.5" })).toMatchObject({ ok: false, error: expect.stringContaining("whole number") });
    expect(bad({ elementsPerJob: "10001" })).toMatchObject({ ok: false });
    expect(bad({ changePercent: "0" })).toMatchObject({ ok: false });
    expect(bad({ changePercent: "10001" })).toMatchObject({ ok: false });
    expect(bad({ changePercent: "abc" })).toMatchObject({ ok: false });
    expect(bad({ changePercent: "NaN" })).toMatchObject({ ok: false });
    expect(bad({ changePercent: "Infinity" })).toMatchObject({ ok: false });
  });

  it("starts the form from the limits in force", () => {
    expect(limitsForm({ maxConfigJobsPerHour: 20, maxElementsPerJob: null, maxChangePercent: 10 })).toEqual({ jobsPerHour: "20", elementsPerJob: "", changePercent: "10" });
    expect(limitsForm(null)).toEqual({ jobsPerHour: "", elementsPerJob: "", changePercent: "" });
  });
});
