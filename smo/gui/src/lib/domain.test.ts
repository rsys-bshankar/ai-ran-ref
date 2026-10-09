import { describe, expect, it } from "vitest";

import type { ModuleStatus } from "../api/types";

import { REFUSAL_CODES, REFUSAL_MEANING, APPROVAL_MEANING, DISPOSITION_MEANING, approvalPolicyForm, approvalPolicyPayload, describeApprovalPolicy, INTEGRITY_MEANING, completionRoute, decisionQuery, describeChange, describeElements, timeLeft, countBySeverity, canRollback, describeDifferences, describeGuardResult, describeLimits, describePlace, describeScope, describeSeconds, kpiNameProblem, limitsForm, limitsPayload, parseCounters, schedulePayload, stagedPayload, waveActions, waveProgress, metricSeries, moduleRows, modelActions, numericMetricKeys, packageActions, parseJsonObject, pipelineSteps, sortAlarms, splitList } from "./domain";

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

describe("staged CM jobs", () => {
  const now = new Date("2026-10-04T12:00:00Z");
  it("offers the wave actions only to a halted job, and forces a pause that has not elapsed", () => {
    expect(waveActions({ status: "COMPLETED", haltedReason: null, nextWaveAt: null }, now)).toEqual([]);
    expect(waveActions({ status: "PROCESSING", haltedReason: null, nextWaveAt: null }, now)).toEqual([]);
    expect(waveActions({ status: "HALTED", haltedReason: "WAVE_PAUSE", nextWaveAt: "2026-10-04T13:00:00Z" }, now)).toEqual([
      { action: "continue", force: true }, { action: "halt", force: false }, { action: "abort", force: false }]);
    expect(waveActions({ status: "HALTED", haltedReason: "WAVE_PAUSE", nextWaveAt: "2026-10-04T11:00:00Z" }, now)[0]).toEqual({ action: "continue", force: false });
  });
  it("offers continue and abort, not halt, after a failed gate or an operator halt", () => {
    for (const reason of ["GATE_FAILED", "OPERATOR_HALT", "REVERT_REFUSED"]) {
      expect(waveActions({ status: "HALTED", haltedReason: reason, nextWaveAt: null }, now).map((a) => a.action)).toEqual(["continue", "abort"]);
    }
  });
  it("shows the wave progress", () => {
    expect(waveProgress({ waveCount: 4, currentWave: 2, waveSize: 2 })).toBe("Wave 2 of 4");
    expect(waveProgress({ waveCount: 1, currentWave: 1, waveSize: null })).toBe("One wave");
    expect(waveProgress({})).toBe("One wave");
    expect(waveProgress({ waveCount: 3, currentWave: 9, waveSize: 1 })).toBe("Wave 3 of 3");
  });
  it("lets a job be rolled back only when it applied something", () => {
    expect(canRollback({ subChanges: [{ managedElementRef: "a", operation: "merge", status: "REJECTED", rejectionReason: "x" }] })).toBe(false);
    expect(canRollback({ subChanges: [{ managedElementRef: "a", operation: "merge", status: "APPLIED", rejectionReason: null }] })).toBe(true);
    expect(canRollback({ subChanges: [] })).toBe(false);
  });
});

describe("rollback and the KPI guard in words", () => {
  it("lists what changed since the job wrote it", () => {
    expect(describeDifferences({ changedSince: [{ managedElementRef: "ME-1", managedFunctionRef: null, attribute: "txPower", expected: 20, actual: "99" }] }))
      .toEqual(['ME-1 txPower: expected 20, found "99"']);
    expect(describeDifferences({ changedSince: [] })).toEqual([]);
  });
  it("says what the guard found", () => {
    expect(describeGuardResult(null, null)).toBe("Not checked yet");
    expect(describeGuardResult({ verdict: "OK" }, "t")).toBe("The KPI held");
    expect(describeGuardResult({ verdict: "REGRESSED", reverted: true }, "t")).toContain("rolled back");
    expect(describeGuardResult({ verdict: "REGRESSED", reverted: false, error: "2 value(s) differ" }, "t")).toBe("The KPI regressed; not reverted: 2 value(s) differ");
    expect(describeGuardResult({ verdict: "INSUFFICIENT_DATA" }, null)).toContain("tried again");
    expect(describeGuardResult({ verdict: "INSUFFICIENT_DATA" }, "t")).not.toContain("tried again");
    expect(describeGuardResult({ verdict: "ERROR", error: "boom" }, "t")).toBe("The check failed: boom");
  });
  it("writes intervals the way an operator reads them", () => {
    expect([90, 600, 3600, 7200, 172800, 61].map(describeSeconds)).toEqual(["90 s", "10 min", "1 h", "2 h", "2 d", "61 s"]);
  });
});

describe("KPI definition and staged-job forms", () => {
  it("checks the counters text before it is sent", () => {
    expect(parseCounters('[{"counter":"ok","variable":"ok","aggregation":"sum"}]')).toEqual({ ok: true, value: [{ counter: "ok", variable: "ok", aggregation: "sum" }] });
    expect(parseCounters('[{"counter":"ok"}]')).toEqual({ ok: true, value: [{ counter: "ok", variable: null, aggregation: "sum" }] });
    expect(parseCounters("{}")).toMatchObject({ ok: false, error: "Counters must be a JSON array" });
    expect(parseCounters("[1]")).toMatchObject({ ok: false });
    expect(parseCounters('[{"counter":"ok","aggregation":"median"}]')).toMatchObject({ ok: false, error: expect.stringContaining("aggregation") });
    expect(parseCounters("not json")).toMatchObject({ ok: false, error: expect.stringContaining("Not valid JSON") });
  });
  const blank = { waveSize: "", wavePauseSeconds: "", gateMaxNewAlarms: "", onGateFailure: "halt" as const };
  const guard = { kpi: "succ", baselineMinutes: "60", observationMinutes: "60", maxRegressionPercent: "10", direction: "higher" as const, revert: true };
  it("leaves a plain job plain", () => {
    expect(stagedPayload(blank, null)).toEqual({ ok: true, body: {} });
    expect(stagedPayload(blank, { ...guard, kpi: "" })).toEqual({ ok: true, body: {} });
  });
  it("builds the staged and guard fields", () => {
    expect(stagedPayload({ waveSize: "2", wavePauseSeconds: "300", gateMaxNewAlarms: "0", onGateFailure: "revert" }, guard)).toEqual({ ok: true, body: {
      waveSize: 2, wavePauseSeconds: 300, gateMaxNewAlarms: 0, onGateFailure: "revert",
      kpiGuard: { kpi: "succ", baselineMinutes: 60, observationMinutes: 60, maxRegressionPercent: 10, direction: "higher", revert: true } } });
  });
  it("refuses what the backend would refuse", () => {
    expect(stagedPayload({ ...blank, waveSize: "0" }, null)).toMatchObject({ ok: false });
    expect(stagedPayload({ ...blank, waveSize: "1.5" }, null)).toMatchObject({ ok: false });
    expect(stagedPayload({ ...blank, wavePauseSeconds: "-1" }, null)).toMatchObject({ ok: false });
    expect(stagedPayload(blank, { ...guard, observationMinutes: "0" })).toMatchObject({ ok: false });
    expect(stagedPayload(blank, { ...guard, baselineMinutes: "10081" })).toMatchObject({ ok: false });
    expect(stagedPayload(blank, { ...guard, maxRegressionPercent: "-5" })).toMatchObject({ ok: false });
    expect(stagedPayload(blank, { ...guard, maxRegressionPercent: "abc" })).toMatchObject({ ok: false });
  });
});

describe("KPI names and schedules", () => {
  it("applies the backend's name rule", () => {
    expect(kpiNameProblem("dl_prb_utilization")).toBeNull();
    expect(kpiNameProblem("a.b-c_1")).toBeNull();
    expect(kpiNameProblem("1abc")).not.toBeNull();
    expect(kpiNameProblem("has space")).not.toBeNull();
    expect(kpiNameProblem("")).not.toBeNull();
    expect(kpiNameProblem("a".repeat(65))).not.toBeNull();
    expect(kpiNameProblem("standard")).toContain("seeded");
  });
  const form = { kpi: "succ", intervalSeconds: "600", lookbackSeconds: "", groupBy: "cell", managedElementRef: "", cellId: "", enabled: true };
  it("builds the schedule body and leaves blanks out", () => {
    expect(schedulePayload(form)).toEqual({ ok: true, body: { kpi: "succ", intervalSeconds: 600, groupBy: "cell", enabled: true } });
    expect(schedulePayload({ ...form, lookbackSeconds: "3600", managedElementRef: " ME-1 ", cellId: "7", enabled: false })).toEqual({
      ok: true, body: { kpi: "succ", intervalSeconds: 600, lookbackSeconds: 3600, groupBy: "cell", enabled: false, managedElementRef: "ME-1", cellId: "7" } });
  });
  it("refuses what the backend would refuse", () => {
    expect(schedulePayload({ ...form, kpi: "" })).toMatchObject({ ok: false });
    expect(schedulePayload({ ...form, intervalSeconds: "59" })).toMatchObject({ ok: false });
    expect(schedulePayload({ ...form, intervalSeconds: "86401" })).toMatchObject({ ok: false });
    expect(schedulePayload({ ...form, intervalSeconds: "60.5" })).toMatchObject({ ok: false });
    expect(schedulePayload({ ...form, lookbackSeconds: "30" })).toMatchObject({ ok: false });
    expect(schedulePayload({ ...form, lookbackSeconds: "604801" })).toMatchObject({ ok: false });
  });
});

describe("module status table (PR-OBS-8.3)", () => {
  const up = (module: string, extra: Partial<ModuleStatus> = {}): ModuleStatus => ({
    module, healthy: true, latencyMs: 4, statusCode: 200, error: null, ready: true,
    version: "1.4.0", buildSha: "0123456789abcdef", builtAt: "2026-10-06T08:00:00Z", ...extra,
  });

  it("shows readiness, version and the commit shortened to seven characters", () => {
    expect(moduleRows([up("sme")])).toEqual([
      { module: "sme", readiness: "READY", version: "1.4.0", buildSha: "0123456", builtAt: "2026-10-06T08:00:00Z", skewed: false },
    ]);
  });

  it("tells down, not-ready and not-asked apart", () => {
    const rows = moduleRows([
      up("nfo", { healthy: false, ready: null, version: null, buildSha: null, builtAt: null, error: "unreachable", statusCode: null }),
      up("dme", { ready: false }),
      up("sme", { ready: null }),
    ]);
    expect(rows.map((r) => r.readiness)).toEqual(["DOWN", "NOT READY", "UNKNOWN"]);
    expect(rows[0]).toMatchObject({ version: "—", buildSha: "—", builtAt: "—", skewed: false });
  });

  it("shows a dash for a module with no /version or an image built without the arguments", () => {
    const rows = moduleRows([up("a", { version: null, buildSha: null, builtAt: null }), up("b", { version: "unknown", buildSha: "unknown", builtAt: "unknown" })]);
    expect(rows.every((r) => r.version === "—" && r.buildSha === "—" && r.builtAt === "—" && !r.skewed)).toBe(true);
  });

  it("flags the modules that run a different commit than most (a rolling upgrade in progress)", () => {
    const rows = moduleRows([up("a"), up("b"), up("c", { buildSha: "fedcba9876543210" })]);
    expect(rows.map((r) => r.skewed)).toEqual([false, false, true]);
  });
});


describe("approvals and decision records (AI-11, AI-13)", () => {
  const now = Date.parse("2026-10-09T10:00:00Z");
  const at = (minutes: number) => new Date(now + minutes * 60_000).toISOString();

  it("says how long a request has before it lapses", () => {
    expect(timeLeft(at(-1), now)).toBe("overdue");
    expect(timeLeft(at(0.5), now)).toBe("under a minute");
    expect(timeLeft(at(25), now)).toBe("in 25 min");
    expect(timeLeft(at(125), now)).toBe("in 2 h 5 min");
    expect(timeLeft(at(180), now)).toBe("in 3 h");
    expect(timeLeft(at(60 * 24 * 5), now)).toBe("in 5 days");
    expect(timeLeft(null, now)).toBe("—");
    expect(timeLeft("not a date", now)).toBe("not a date");
  });

  it("lists the first three elements and counts the rest", () => {
    expect(describeElements([])).toBe("—");
    expect(describeElements(null)).toBe("—");
    expect(describeElements(["A", "B", "C"])).toBe("A, B, C");
    expect(describeElements(["A", "B", "C", "D", "E"])).toBe("A, B, C +2");
  });

  it("describes a change as the element or function, the operation and the values", () => {
    expect(describeChange({ managedElementRef: "ME-1", managedFunctionRef: "NRCellDU=101", operation: "merge", attributeChanges: { txPower: 20, tilt: 3 } })).toBe("ME-1 / NRCellDU=101 merge txPower=20, tilt=3");
    expect(describeChange({ managedElementRef: "ME-2" })).toBe("ME-2 merge");
    expect(describeChange({ managedElementRef: "ME-2", operation: "delete" })).toBe("ME-2 delete");
    expect(describeChange({ managedElementRef: "ME-3", attributeChanges: { list: [1, 2] } })).toBe("ME-3 merge list=[1,2]");
  });

  it("builds the decision query from the filters, leaving blanks out and never asking for a count", () => {
    expect(decisionQuery({ invoker: " ", disposition: "", model: "" }, 0, 25)).toEqual({ limit: 25, offset: 0, total: false });
    expect(decisionQuery({ invoker: " rapp-1 ", disposition: "APPROVED", model: "m 1.0", job: "j-1", approval: "a-1" }, 50, 25)).toEqual({
      limit: 25, offset: 50, total: false, invoker_id: "rapp-1", disposition: "APPROVED", model_version: "m 1.0", job_id: "j-1", approval_id: "a-1" });
  });

  it("explains every status, outcome and integrity result the API can return", () => {
    for (const status of ["PENDING", "APPROVED", "REJECTED", "EXPIRED", "REFUSED"]) expect(APPROVAL_MEANING[status]).toBeTruthy();
    for (const outcome of ["DIRECT", "APPROVED", "ROLLBACK", "REJECTED", "EXPIRED", "REFUSED"]) expect(DISPOSITION_MEANING[outcome]).toBeTruthy();
    for (const result of ["VERIFIED", "UNCHAINED", "MISMATCH"]) expect(INTEGRITY_MEANING[result]).toBeTruthy();
  });

  it("describes an approval policy and builds the form and the request body from it", () => {
    expect(describeApprovalPolicy(null)).toBe("Writes at once");
    expect(describeApprovalPolicy({ timeoutSeconds: 3600, onTimeout: "EXPIRE" })).toBe("Held for approval · lapses after 1 h (expires)");
    expect(describeApprovalPolicy({ timeoutSeconds: 5400, onTimeout: "REJECT" })).toBe("Held for approval · lapses after 90 min (rejected)");
    expect(approvalPolicyForm(null)).toEqual({ minutes: "60", onTimeout: "EXPIRE" });          // the conservative default
    expect(approvalPolicyForm({ timeoutSeconds: 1800, onTimeout: "REJECT" })).toEqual({ minutes: "30", onTimeout: "REJECT" });
    expect(approvalPolicyPayload({ minutes: " 30 ", onTimeout: "REJECT" })).toEqual({ ok: true, body: { timeoutSeconds: 1800, onTimeout: "REJECT" } });
    expect(approvalPolicyPayload({ minutes: "10080", onTimeout: "EXPIRE" }).ok).toBe(true);
    for (const bad of ["", " ", "0", "-5", "1.5", "10081", "soon"]) expect(approvalPolicyPayload({ minutes: bad, onTimeout: "EXPIRE" }).ok).toBe(false);
  });
});

describe("tenant and region scope (SEC-10)", () => {
  it("describes a scope claim: each axis it restricts, or that there is none", () => {
    expect(describeScope(null)).toBe("Unscoped (every managed element)");
    expect(describeScope(undefined)).toBe("Unscoped (every managed element)");
    expect(describeScope({})).toBe("Unscoped (every managed element)");
    expect(describeScope({ regions: [], tenants: [] })).toBe("Unscoped (every managed element)");
    expect(describeScope({ regions: ["eu-west", "eu-north"] })).toBe("regions eu-west, eu-north");
    expect(describeScope({ tenants: ["acme"] })).toBe("tenants acme");
    expect(describeScope({ regions: ["eu-west"], tenants: ["acme", "globex"] })).toBe("regions eu-west · tenants acme, globex");
  });

  it("describes where an element is: region / tenant, a dash for a part that is not set", () => {
    expect(describePlace(null)).toBe("—");
    expect(describePlace({})).toBe("—");
    expect(describePlace({ region: null, tenant: null })).toBe("—");
    expect(describePlace({ region: "eu-west", tenant: "acme" })).toBe("eu-west / acme");
    expect(describePlace({ region: "eu-west", tenant: null })).toBe("eu-west / —");
    expect(describePlace({ tenant: "acme" })).toBe("— / acme");
  });

  it("explains every refusal code the safeguards page can filter by, including a scope refusal", () => {
    expect(REFUSAL_CODES).toContain("SCOPE_DENIED");
    for (const code of REFUSAL_CODES) expect(REFUSAL_MEANING[code]).toMatch(/\S/);
  });
});
