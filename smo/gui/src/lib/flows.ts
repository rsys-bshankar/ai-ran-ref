/** The end-to-end journeys in smo/docs/call-flows (BRIEF §4c: 01–04, 06–10, 15, 16, 19; 02 with its phases), as pure functions from
 * live SMO state to step status. The Lifecycle page renders these; keeping
 * them pure keeps "is this step done?" testable without a browser.
 *
 * A step is `done` when the entity state proves it happened, `current` when it
 * is the next thing to do, `failed` when the state shows it went wrong, and
 * `todo` when it is further ahead. `blocked` marks a step an earlier failure
 * makes impossible. */
import type {
  AnalyticsProducer, AnalyticsReport, AnalyticsSubscription, ConfigJob, FaultReport,
  InferenceJob, Instance, Intent, IntentReport, InventorySubscription, LcmOperation, MlmfReport, MlmfSubscription, Model, ModelLifecycle, Monitor,
  NfDeployment, O1Endpoint, OCloudResource, Package, PackageUsage, PerfReport, RemedialAction, Rmih, ServiceOrder, SwmJob, TrainingJob,
} from "../api/types";
import type { StepState } from "../kit/Timeline";
import { MODEL_PIPELINE, RUNTIME_PIPELINE } from "./domain";

export type StepStatus = "done" | "current" | "todo" | "failed" | "blocked" | "warn";

/** One step of a flow, evaluated for one subject: who performs it, its status and a detail proved by live state. */
export interface FlowStep {
  id: string;
  title: string;
  actor: string;          // who performs it (Operator, Onboarding, NFO, rApp container, ...)
  status: StepStatus;
  detail?: string;
  phase?: string;         // flow 02's phases (Build & certify, Serve, End of life); absent elsewhere
}

/** A flow of the catalogue: its id and number, title, call-flow doc, the kind of subject it follows and the modules it crosses. */
export interface FlowDef {
  id: string;
  number: string;
  title: string;
  doc: string;            // smo/docs/call-flows/<doc>
  subject: string;        // what the operator picks to track
  modules: string[];
}

export const FLOWS: FlowDef[] = [
  { id: "01", number: "01", title: "rApp onboarding → running instance", doc: "01-rapp-onboarding-to-deployment.md", subject: "package", modules: ["onboarding", "rapp-mgmt", "nfo", "focom", "sme", "dme"] },
  { id: "02", number: "02", title: "AI/ML model: register → train → certify → deploy → infer → monitor", doc: "02-aiml-model-train-to-inference.md", subject: "model", modules: ["mlmr", "aimgf", "mllf", "dme"] },
  { id: "03", number: "03", title: "Configuration write, schema-checked, fleet-aware", doc: "03-config-write-with-schema-check.md", subject: "config job", modules: ["ran-nf-oam"] },
  { id: "04", number: "04", title: "Closed-loop assurance: monitor → decide → remediate → escalate", doc: "04-closed-loop-assurance.md", subject: "assurance monitor", modules: ["so-smos", "sa-smos", "mdaf", "ran-nf-oam", "nfo"] },
  { id: "06", number: "06", title: "Package lifecycle: onboard → prime → deprecate → delete", doc: "06-package-lifecycle.md", subject: "package", modules: ["onboarding", "rapp-mgmt"] },
  { id: "07", number: "07", title: "rApp instance lifecycle: report → fault → recover → upgrade → terminate", doc: "07-rapp-instance-lifecycle.md", subject: "rApp instance", modules: ["rapp-mgmt"] },
  { id: "08", number: "08", title: "RAN Analytics: producer → report → subscriber query", doc: "08-ran-analytics-data-production.md", subject: "analytics type", modules: ["ran-analytics", "mdaf", "sme"] },
  { id: "09", number: "09", title: "Intent registration → fulfilment reporting → admin state", doc: "09-intent-service-intent-flow.md", subject: "intent", modules: ["intent-service"] },
  { id: "10", number: "10", title: "SO SMOS multi-step order: INFRA → TRAINING → DEPLOY", doc: "10-so-smos-multi-step-infra-training-deploy.md", subject: "service order", modules: ["so-smos", "focom", "aimgf", "nfo"] },
  { id: "15", number: "15", title: "NFO workload: instantiate → scale → heal → terminate", doc: "15-nfo-workload-lifecycle.md", subject: "NF deployment", modules: ["nfo", "focom"] },
  { id: "16", number: "16", title: "FOCOM resource & inventory: subscribe → provision → notify → deprovision", doc: "16-focom-resource-inventory-lifecycle.md", subject: "O-Cloud resource", modules: ["focom", "nfo"] },
  { id: "19", number: "19", title: "RAN software job: download → install → activate", doc: "19-software-management-job-lifecycle.md", subject: "software job", modules: ["ran-nf-oam"] },
];

/** The kit's step state (`kit/Timeline`) of a flow step status: the boards and the rApp detail steppers draw through it. */
export function toStepState(status: StepStatus): StepState {
  return ({ done: "done", current: "now", todo: "todo", failed: "fail", blocked: "block", warn: "warn" } as const)[status];
}

/** Marks the first not-done step `current` (unless something failed), and
 * everything after a failure `blocked`. Evaluators set only done/failed/warn
 * and leave the rest `todo`. */
export function settle(steps: FlowStep[]): FlowStep[] {
  let seenCurrent = false;
  let failed = false;
  return steps.map((s) => {
    if (failed && s.status === "todo") return { ...s, status: "blocked" };
    if (s.status === "failed") { failed = true; return s; }
    if (s.status === "todo" && !seenCurrent) { seenCurrent = true; return { ...s, status: "current" }; }
    return s;
  });
}

/** How far a flow is: done (warned steps count as done), total, whether a step failed and whether every step is done. */
export function progress(steps: FlowStep[]): { done: number; total: number; failed: boolean; complete: boolean } {
  const done = steps.filter((s) => s.status === "done" || s.status === "warn").length;
  return { done, total: steps.length, failed: steps.some((s) => s.status === "failed"), complete: done === steps.length };
}

const step = (id: string, title: string, actor: string, ok: boolean | "failed" | "warn", detail?: string): FlowStep =>
  ({ id, title, actor, status: ok === "failed" ? "failed" : ok === "warn" ? "warn" : ok ? "done" : "todo", detail });

const PKG_ONBOARDED = ["AVAILABLE", "PRIMING", "PRIMED", "DEPRIMING", "DEPRECATED", "DELETING"];

// ---------------------------------------------------------------- 01

/** Flow 01 (rApp onboarding → running instance) for a package, its newest instance and that instance's NFO deployment. */
export function flow01(pkg: Package | undefined, instance: Instance | undefined, deployment: NfDeployment | undefined): FlowStep[] {
  if (!pkg) return settle([step("onboard", "OnboardPackage(location)", "Operator → Onboarding", false)]);
  const onboarded = PKG_ONBOARDED.includes(pkg.state);
  return settle([
    step("onboard", "OnboardPackage(location)", "Operator → Onboarding", true, `package ${pkg.name} ${pkg.version}`),
    step("validate", "Fetch CSAR, TOSCA.meta, signature → AVAILABLE", "Onboarding", pkg.state === "FAILED" ? "failed" : onboarded, `state ${pkg.state}`),
    step("descriptor", "NFO CreateDescriptor (NFDeploymentDescriptor)", "Onboarding → NFO", !!pkg.nfDeploymentDescriptorId),
    step("create", "CreateInstance(packageId, config)", "Operator → rApp Mgmt", !!instance, instance ? `instance ${instance.instanceId.slice(0, 8)}` : undefined),
    step("instantiate", "NFO Instantiate (cluster via FOCOM inventory)", "rApp Mgmt → NFO → FOCOM",
      deployment ? (deployment.state === "ABNORMAL" ? "failed" : deployment.state === "RUNNING") : !!instance?.workloadRef,
      deployment ? `deployment ${deployment.name}: ${deployment.state} on ${deployment.clusterId}` : undefined),
    step("bootstrap", "R1 bootstrap, SME/DME registration, bootstrap-complete", "rApp container → R1 → rApp Mgmt",
      instance ? (instance.state === "FAULTED" ? "failed" : ["RUNNING", "UPGRADING"].includes(instance.state)) : false,
      instance ? `instance ${instance.state}` : undefined),
  ]);
}

// ---------------------------------------------------------------- 02

const reached = (state: string | undefined, target: string) => {
  if (!state) return false;
  if (state === "DEPRECATED" || state === "RETIRED" || state === "FAILED") return true;
  return MODEL_PIPELINE.indexOf(state as (typeof MODEL_PIPELINE)[number]) >= MODEL_PIPELINE.indexOf(target as (typeof MODEL_PIPELINE)[number]);
};

const runtimeReached = (state: string | undefined, target: string) => {
  if (!state) return false;
  return RUNTIME_PIPELINE.indexOf(state as (typeof RUNTIME_PIPELINE)[number]) >= RUNTIME_PIPELINE.indexOf(target as (typeof RUNTIME_PIPELINE)[number]);
};

/** Flow 02 (AI/ML model: register → train → certify → deploy → infer → monitor) for a model, its lifecycle, jobs, MLMF subscriptions and reports. */
export function flow02(model: Model | undefined, lifecycle: ModelLifecycle | undefined, jobs: TrainingJob[], inference: InferenceJob[], subs: MlmfSubscription[], reports: MlmfReport[]): FlowStep[] {
  if (!model) return settle([step("register", "RegisterModel(modelType, version)", "Producer → AI/ML", false)]);
  const s = lifecycle?.modelLifecycleState;
  const rs = lifecycle?.runtimeLifecycleState;
  const nodeGroups = lifecycle?.clearedNodeGroups ?? [];
  const inferenceDone = inference.find((j) => j.status === "COMPLETED");
  const inferenceFailed = inference.length > 0 && inference.every((j) => j.status === "FAILED");
  const breached = reports.filter((r) => r.breachedFloor).length;
  return settle([
    step("register", "RegisterModel(modelType, version)", "Producer → AI/ML", true, `${model.modelType} v${model.version}`),
    step("train", "RequestTraining → TRAINING", "Producer → AI/ML (MLTF)", jobs.length > 0 || reached(s, "TRAINING"), jobs.length ? `${jobs.length} training job(s)` : undefined),
    step("tested", "training-jobs/{id}/complete → TRAINED → RequestValidation → VALIDATED", "MLVF", reached(s, "VALIDATED")),
    step("emulated", "RequestEmulation → EMULATING → EMULATED", "MLEF", reached(s, "EMULATED")),
    step("certified", "governance: submit → approve → certify → CERTIFIED", "AIMgF", reached(s, "CERTIFIED")),
    step("deploy", "RequestModelDeployment(nodeGroups)", "Producer → MLLF", nodeGroups.length > 0, nodeGroups.join(", ") || undefined),
    step("loaded", "runtime/deploy → DEPLOYED (AIMgF + NFO)", "AIMgF", runtimeReached(rs, "DEPLOYED")),
    step("active", "runtime/activate → ACTIVE", "AIMgF", runtimeReached(rs, "ACTIVE")),
    step("infer", "RequestInference → COMPLETED (result via DME)", "Consumer → MLEF",
      inferenceDone ? true : inferenceFailed ? "warn" : false, inference.length ? `${inference.length} job(s): ${inference.map((j) => j.status).join(", ")}` : undefined),
    step("monitor", "SubscribePerformanceMonitoring(guardKpiFloor)", "Producer → MLMF", subs.length > 0, subs.length ? `${subs.length} subscription(s)` : undefined),
    step("report", "ReportPerformance → breachedFloor → retrain", "Producer → MLMF", reports.length === 0 ? false : breached ? "warn" : true,
      reports.length ? `${reports.length} report(s), ${breached} under the floor${breached ? " (retrain triggered)" : ""}` : undefined),
  ]);
}

// ---------------------------------------------------------------- 03

/** Flow 03 (configuration write, schema-checked) for a config job, given the O1 endpoints. */
export function flow03(endpoints: O1Endpoint[], job: ConfigJob | undefined): FlowStep[] {
  const healthy = endpoints.filter((e) => e.healthStatus === "ACTIVE");
  const subs = job?.subChanges ?? [];
  const applied = subs.filter((c) => c.status === "APPLIED").length;
  const rejected = subs.filter((c) => c.status === "REJECTED").length;
  const aggregate = job?.status;
  return settle([
    step("registry", "O1 adaptors register in the MnS Registry NRM", "O1 Adaptor → RAN NF OAM", endpoints.length > 0, `${endpoints.length} endpoint(s)`),
    step("health", "Registry tracks endpoint health (heartbeats)", "RAN NF OAM", healthy.length > 0 ? true : endpoints.length ? "warn" : false,
      endpoints.length ? `${healthy.length}/${endpoints.length} ACTIVE` : undefined),
    step("write", "WriteConfigurationChanges(scope, changes[])", "rApp/Operator → RAN NF OAM", !!job, job ? `job ${job.jobId.slice(0, 8)}` : undefined),
    step("schema", "MSAC gate + schema check → PROCESSING", "RAN NF OAM", !!job && aggregate !== "PENDING"),
    step("dispatch", "Per-ME NETCONF <edit-config> (sub-changes)", "RAN NF OAM → O1 Adaptor", subs.length === 0 ? false : rejected && !applied ? "failed" : rejected ? "warn" : true,
      subs.length ? `${applied} APPLIED, ${rejected} REJECTED` : undefined),
    step("aggregate", "Aggregate job status", "RAN NF OAM",
      aggregate === "COMPLETED" ? true : aggregate === "PARTIAL_SUCCESS" ? "warn" : aggregate === "FAILED" ? "failed" : false, aggregate),
  ]);
}

// ---------------------------------------------------------------- 04

/** Flow 04 (closed-loop assurance) for a monitor, its service order and remedial actions, and the number of MDAF and MLMF reports. */
export function flow04(monitor: Monitor | undefined, order: ServiceOrder | undefined, actions: RemedialAction[], analyticsReports: number, mlmfReports: number): FlowStep[] {
  const orderDone = order ? order.steps.every((s) => s.status === "COMPLETED") : false;
  const orderFailed = order?.steps.some((s) => s.status === "FAILED");
  const resolved = actions.filter((a) => a.outcome === "RESOLVED");
  const escalated = actions.filter((a) => a.outcome === "ESCALATED");
  const groupScoped = !!monitor?.targetCoordinationGroupId;
  return settle([
    step("order", groupScoped ? "Model coordination group in service" : "SubmitServiceOrder(CONFIG, DEPLOY)", groupScoped ? "AI/ML Workflow" : "Operator → SO SMOS",
      groupScoped ? true : !order ? (monitor?.targetOrderId ? "warn" : false) : orderFailed ? "failed" : orderDone,
      order ? order.steps.map((s) => `${s.stepType} ${s.status}`).join(", ") : undefined),
    step("monitor", "RegisterAssuranceMonitor(target, thresholds)", "SA SMOS", !!monitor,
      monitor ? Object.entries(monitor.thresholds).map(([k, v]) => `${k} ≥ ${v}`).join(", ") || "no thresholds" : undefined),
    step("input", "MDAF / MLMF reports arrive", "RAN Analytics / MLMF → SA SMOS", analyticsReports + mlmfReports > 0,
      `${analyticsReports} MDAF, ${mlmfReports} MLMF report(s)`),
    step("remediate", "EvaluateThresholds → ExecuteRemedialAction", "SA SMOS → RAN NF OAM / NFO / AI/ML", resolved.length > 0 ? true : actions.length ? "warn" : false,
      actions.length ? actions.map((a) => `${a.actionType} ${a.outcome}`).join(", ") : undefined),
    step("escalate", "EscalateToOperator (when remediation can't resolve)", "SA SMOS → Operator", escalated.length ? "warn" : resolved.length > 0,
      escalated.length ? `${escalated.length} escalation(s)` : undefined),
  ]);
}

// ---------------------------------------------------------------- 06

/** Flow 06 (package lifecycle and the cascade-delete guard) for a package, its usage registrations and the instances using it. */
export function flow06(pkg: Package | undefined, usage: PackageUsage[], instanceCount: number): FlowStep[] {
  if (!pkg) return settle([step("onboard", "OnboardPackage", "Operator → Onboarding", false)]);
  const active = usage.filter((u) => u.active).length;
  const failed = pkg.state === "FAILED";
  const primed = ["PRIMING", "PRIMED", "DEPRIMING"].includes(pkg.state);
  return settle([
    step("onboard", "OnboardPackage → AVAILABLE or FAILED", "Onboarding", failed ? "warn" : PKG_ONBOARDED.includes(pkg.state), `state ${pkg.state}`),
    // priming is optional: an AVAILABLE package is deployable as it is
    step("prime", "Prime (optional): AVAILABLE → PRIMED", "Operator → Onboarding", failed || pkg.state !== "ONBOARDING",
      failed ? undefined : primed ? pkg.state : "optional — not primed"),
    step("refuse", failed ? "CreateInstance refused (409: never AVAILABLE or PRIMED)" : "Package usable: instances may reference it", "rApp Mgmt",
      failed ? true : pkg.state !== "ONBOARDING", failed ? undefined : `${instanceCount} instance(s)`),
    step("deprecate", "Deprecate (AVAILABLE → DEPRECATED)", "Operator → Onboarding",
      failed || ["DEPRECATED", "DELETING"].includes(pkg.state), primed ? "deprime first (refused while usage is active)" : undefined),
    step("guard", "Cascade-delete guard: no active usage registrations", "Onboarding",
      // active usage is a hard stop for delete (and deprime): shown as the
      // failing step so the delete step reads as blocked, not next
      failed ? true : active === 0 ? usage.length > 0 || pkg.state === "DELETING" : "failed",
      `${active} active / ${usage.length} usage registration(s)${active ? " — delete is blocked" : ""}`),
    step("delete", "DeletePackage → DELETING", "Operator → Onboarding", pkg.state === "DELETING"),
  ]);
}

// ---------------------------------------------------------------- 07

/** Flow 07 (rApp instance lifecycle: report → fault → recover → upgrade → terminate) for an instance, its performance reports and faults. */
export function flow07(instance: Instance | undefined, perf: PerfReport[], faults: FaultReport[]): FlowStep[] {
  if (!instance) return settle([step("running", "Instance RUNNING (call flow 01)", "rApp Mgmt", false)]);
  const critical = faults.filter((f) => f.severity === "critical");
  const minor = faults.filter((f) => f.severity !== "critical");
  const s = instance.state;
  const gone = s === "UNDEPLOYED";
  return settle([
    step("running", "Instance RUNNING (call flow 01)", "rApp Mgmt", s !== "DEPLOYING" || perf.length + faults.length > 0, `state ${s}`),
    step("perf", "ReportPerformance(metrics) — no state change", "rApp container → R1 → rApp Mgmt", perf.length > 0 || gone, perf.length ? `${perf.length} report(s)` : undefined),
    step("minor", "ReportFault(non-critical) — recorded only", "rApp container → rApp Mgmt", minor.length > 0 || gone, minor.length ? `${minor.length} fault(s)` : undefined),
    // a crash is an expected branch of this flow, not a dead end: shown as a
    // warning so RECOVER (the next step) stays actionable instead of blocked
    step("crash", "ReportFault(critical) → FAULTED (CRASH)", "rApp container → rApp Mgmt", critical.length > 0 ? (s === "FAULTED" ? "warn" : true) : gone,
      critical.length ? `${critical.length} critical fault(s)` : undefined),
    step("recover", "RECOVER → DEPLOYING → re-bootstrap → RUNNING", "Operator → rApp Mgmt",
      critical.length > 0 && ["RUNNING", "UPGRADING", "UNDEPLOYED"].includes(s) ? true : critical.length > 0 && s === "DEPLOYING" ? "warn" : gone,
      critical.length && s === "DEPLOYING" ? "awaiting re-bootstrap" : undefined),
    // upgrade is optional; while UPGRADING the instance awaits upgrade/resolve
    step("upgrade", "Upgrade (optional): UPGRADING → commit or roll back", "Operator → rApp Mgmt",
      s === "UPGRADING" ? "warn" : ["RUNNING", "UNDEPLOYED"].includes(s),
      s === "UPGRADING" ? `awaiting upgrade/resolve (replacement ${instance.pendingUpgradeInstanceId ?? "?"})` : "optional"),
    step("terminate", "Terminate → UNDEPLOYED (NFO terminate, usage/stop)", "Operator → rApp Mgmt → NFO, Onboarding", gone,
      instance.lastTeardown ? `NFO ${instance.lastTeardown.nfoTerminate}, usage/stop ${instance.lastTeardown.usageStop}` : undefined),
  ]);
}

// ---------------------------------------------------------------- 08

/** Flow 08 (RAN Analytics) for an analytics type, given producers, subscriptions, reports and the SME service names of its producers. */
export function flow08(type: string, producers: AnalyticsProducer[], subs: AnalyticsSubscription[], reports: AnalyticsReport[], smeServiceNames: string[]): FlowStep[] {
  const p = producers.filter((x) => x.analyticsType === type);
  const s = subs.filter((x) => x.analyticsType === type);
  const r = reports.filter((x) => x.analyticsType === type);
  const pushed = s.filter((x) => x.notificationDestination);
  return settle([
    step("producer", "RegisterAnalyticsProducer(type, dmeInputTypes)", "Producer → RAN Analytics", p.length > 0, p.map((x) => x.producerId).join(", ") || undefined),
    step("sme", `SME RegisterService(mdaf.${type})`, "RAN Analytics → SME", smeServiceNames.includes(`mdaf.${type}`) ? true : p.length ? "warn" : false,
      smeServiceNames.includes(`mdaf.${type}`) ? "discoverable via service-apis" : p.length ? "not found in SME (provider not registered?)" : undefined),
    step("subscribe", "SubscribeAnalytics(type, notificationDestination?)", "Consumer → RAN Analytics", s.length > 0,
      s.length ? `${s.length} subscription(s), ${pushed.length} with push delivery` : undefined),
    step("publish", "PublishAnalyticsReport → best-effort push", "Producer → RAN Analytics → Consumer", r.length > 0, r.length ? `${r.length} report(s)` : undefined),
    step("query", "QueryAnalyticsReport (pull)", "Consumer → RAN Analytics", r.length > 0),
  ]);
}

// ---------------------------------------------------------------- 09

/** Flow 09 (intent registration → fulfilment reporting → admin state) for an intent, its handlers and reports. */
export function flow09(handlers: Rmih[], intent: Intent | undefined, reports: IntentReport[]): FlowStep[] {
  return settle([
    step("rmih", "RegisterIntentHandlingFunction (framework-internal only)", "SO/SA SMOS → Intent Service", handlers.length > 0,
      handlers.length ? handlers.map((h) => h.rmihId).join(", ") : undefined),
    step("create", "CreateIntent(expectations, rmihId)", "RMIO → Intent Service", !!intent, intent ? `priority ${intent.intentPriority}, RMIO ${intent.rmioId}, RMIH ${intent.rmihId}` : undefined),
    step("dispatch", "Named RMIH notified (consumer-side selection)", "Intent Service → RMIH", !!intent,
      intent ? `addressed to ${intent.rmihId}` : undefined),
    // Wave 6: Intent Service itself writes an initial RECEIVED report at
    // CreateIntent, so the handler has reported once there is more than one.
    step("report", "PublishIntentReport(fulfilment, conflicts)", "RMIH → Intent Service", reports.length > 1, reports.length > 1 ? `${reports.length - 1} handler report(s)` : undefined),
    step("admin", "UpdateIntentAdminState (RMIO only)", "RMIO → Intent Service", intent?.intentAdminState === "DEACTIVATED", intent ? `state ${intent.intentAdminState}` : undefined),
  ]);
}

// ---------------------------------------------------------------- 10

/** Flow 10 (SO SMOS multi-step order) for a service order: one step per order step, fail-fast. */
export function flow10(order: ServiceOrder | undefined): FlowStep[] {
  if (!order) return settle([step("submit", "SubmitServiceOrder(scope, steps[])", "Operator → SO SMOS", false)]);
  const steps: FlowStep[] = [step("submit", "SubmitServiceOrder(scope, steps[])", "Operator → SO SMOS", true, order.scope)];
  order.steps.forEach((s, i) => {
    const status: FlowStep["status"] = s.status === "COMPLETED" ? "done" : s.status === "FAILED" ? "failed" : s.status === "CANCELLED" ? "warn" : "todo";
    steps.push({ id: `step-${i}`, title: `${s.stepType} → ${s.targetModule}`, actor: "SO SMOS dispatch", status,
      detail: s.status === "FAILED" ? `FAILED${s.error ? `: ${s.error}` : ""} — later steps never attempted (fail-fast, no compensation)` : s.status });
  });
  return settle(steps);
}

// ---------------------------------------------------------------- 02 with its phases (17 runtime scale/terminate, 26 governance end of life)

const PHASE_BUILD = "Build & certify";
const PHASE_SERVE = "Serve";
const PHASE_EOL = "End of life";

/** Flow 02 as three phases (BRIEF §4c): the steps of `flow02`, tagged Build & certify or Serve, then runtime scale (call flow 17) and the end
 * of life (call flow 26: roll back, deprecate, terminate the runtime, retire). The extra steps are optional branches: they are `done` only when
 * the lifecycle proves it, and none of them becomes `current` until the model has entered its end of life. */
export function flow02Phases(model: Model | undefined, lifecycle: ModelLifecycle | undefined, jobs: TrainingJob[], inference: InferenceJob[], subs: MlmfSubscription[], reports: MlmfReport[]): FlowStep[] {
  const base = flow02(model, lifecycle, jobs, inference, subs, reports);
  const serveFrom = base.findIndex((s) => s.id === "deploy");
  const tagged = base.map((s, i) => ({ ...s, phase: serveFrom >= 0 && i >= serveFrom ? PHASE_SERVE : PHASE_BUILD }));
  if (!model) return tagged;
  const s = lifecycle?.modelLifecycleState;
  const rs = lifecycle?.runtimeLifecycleState;
  const scale: FlowStep = { ...step("scale", "runtime/scale → SCALING → ACTIVE (NFO scale)", "Producer → AIMgF → NFO", rs === "SCALING" ? "warn" : false,
    rs === "SCALING" ? "scaling now" : "optional — when inference load grows"), phase: PHASE_SERVE };
  const rollback = { ...step("rollback", "advance(ROLLBACK) PROMOTED → CERTIFIED (regression found)", "Admin → AIMgF", false, "optional — the runtime keeps serving"), phase: PHASE_EOL };
  const eol = [
    step("deprecate", "advance(DEPRECATE) → DEPRECATED", "Admin → AIMgF", s === "DEPRECATED" || s === "RETIRED", "no new deployments"),
    step("terminate", "runtime/terminate → TERMINATED (NFO delete)", "Producer → AIMgF → NFO", rs === "TERMINATED" ? true : rs === "TERMINATING" ? "warn" : false,
      rs === "TERMINATING" ? "terminating" : undefined),
    step("retire", "advance(RETIRE) → RETIRED", "Admin → AIMgF", s === "RETIRED", "kept for audit · governance history stays"),
  ];
  const ending = s === "DEPRECATED" || s === "RETIRED" || rs === "TERMINATING" || rs === "TERMINATED";
  return [...tagged, scale, rollback, ...(ending ? settle(eol) : eol).map((x) => ({ ...x, phase: PHASE_EOL }))];
}

// ---------------------------------------------------------------- 15

/** NFO workload lifecycle of one NF deployment, from its state and its LCM operations (`/nfo/deployments/{id}/operations`). A runtime failure
 * (ABNORMAL) is an expected branch, shown as a warning so Heal stays actionable; scale and heal are optional. A synchronous terminate deletes
 * the row, so a deployment that is gone cannot be followed here. */
export function flow15(dep: NfDeployment | undefined, operations: LcmOperation[]): FlowStep[] {
  if (!dep) return settle([step("descriptor", "CreateDescriptor(packageId?, workloadTemplate)", "Caller → NFO", false)]);
  const st = dep.state;
  const ops = (type: string) => operations.filter((o) => o.operationType === type);
  const scaled = ops("SCALE");
  const healed = ops("HEAL");
  const instantiated = !["INITIAL", "INSTANTIATING"].includes(st);
  const abnormal = st === "ABNORMAL";
  return settle([
    step("descriptor", "CreateDescriptor(packageId?, workloadTemplate)", "Caller → NFO", !!dep.nfDeploymentDescriptorId, `descriptor ${dep.nfDeploymentDescriptorId.slice(0, 8)}`),
    step("instantiate", "Instantiate → FOCOM inventory lookup → INSTANTIATING → RUNNING", "Caller → NFO → FOCOM", instantiated, `${dep.name} on ${dep.clusterId}`),
    step("scale", "Scale → UPDATING → RUNNING", "Caller → NFO", st === "UPDATING" ? "warn" : instantiated,
      st === "UPDATING" ? "updating" : scaled.length ? `${scaled.length} scale operation(s)` : "optional — not scaled"),
    step("abnormal", "Runtime failure reported → ABNORMAL", "DMS → NFO", abnormal ? "warn" : instantiated,
      abnormal ? `ABNORMAL${dep.abnormalReason ? `: ${dep.abnormalReason}` : ""}` : healed.length ? "recovered" : "none reported"),
    step("heal", "Heal → RUNNING", "Operator → NFO", abnormal ? false : instantiated, healed.length ? `${healed.length} heal operation(s)` : abnormal ? undefined : "optional"),
    step("terminate", "Terminate → TERMINATING → DELETING (DMS UNINSTALL_COMPLETE)", "Caller → NFO ← DMS", st === "TERMINATING" ? "warn" : st === "DELETING",
      st === "TERMINATING" ? "awaiting the deployment manager (async uninstall)" : undefined),
    step("deleted", "DMS DELETE_COMPLETE → DELETED (descriptor free again)", "DMS → NFO", false),
  ]);
}

// ---------------------------------------------------------------- 16

/** FOCOM resource and inventory lifecycle of one provisioned resource. A subscription matches when it has no resourceTypeId filter or the
 * resource's type; notification is best effort and not recorded, so "notified" means a matching subscription existed. */
export function flow16(resource: OCloudResource | undefined, subs: InventorySubscription[]): FlowStep[] {
  const matching = subs.filter((x) => !x.resourceTypeId || x.resourceTypeId === resource?.resourceTypeId);
  const subscribe = step("subscribe", "Subscribe to inventory (callback, resourceTypeId?)", "NFO → FOCOM", matching.length > 0 ? true : subs.length ? "warn" : false,
    subs.length ? `${subs.length} subscription(s), ${matching.length} matching` : undefined);
  if (!resource) return settle([subscribe, step("provision", "Provision resource (resourceTypeId, description, tags)", "Operator → FOCOM", false)]);
  return settle([
    subscribe,
    step("provision", "Provision resource (resourceTypeId, description, tags)", "Operator → FOCOM", true, `${resource.resourceTypeId} in ${resource.resourcePoolId}`),
    step("notify", "CREATE notified to matching subscriptions (best effort)", "FOCOM → NFO", matching.length ? true : "warn",
      matching.length ? `${matching.length} subscription(s) match · delivery is not recorded` : "no subscription matches — nobody was notified"),
    step("deprovision", "Deprovision → DELETE notified", "Operator → FOCOM → NFO", false, "idempotent: a second delete is still \"deprovisioned\""),
  ]);
}

// ---------------------------------------------------------------- 19

const SWM_PHASES = ["DOWNLOAD", "INSTALL", "ACTIVATE"] as const;

/** RAN software job: three phases, each failing into the one terminal FAILED with `phase` frozen where it failed (no retry: a new job). */
export function flow19(job: SwmJob | undefined): FlowStep[] {
  const create = step("create", "Create software job (managedElementRef, ruInstanceId?) → IN_PROGRESS · DOWNLOAD", "Operator → RAN NF OAM", !!job,
    job ? `${job.managedElementRef}${job.ruInstanceId ? ` · RU ${job.ruInstanceId}` : ""}` : undefined);
  if (!job) return settle([create]);
  const at = SWM_PHASES.indexOf(job.phase as (typeof SWM_PHASES)[number]);
  const phase = (i: number, id: string, title: string, event: string) => step(id, `${title} → ${event}`, "RAN NF OAM → O1 Adaptor → ME",
    job.status === "FAILED" && at === i ? "failed" : job.status === "COMPLETED" || at > i,
    job.status === "FAILED" && at === i ? "PHASE_FAILED — the job is FAILED (terminal); retry with a new job from DOWNLOAD" : undefined);
  return settle([
    create,
    phase(0, "download", "software-download", "DOWNLOAD_OK → phase INSTALL"),
    phase(1, "install", "software-install", "INSTALL_OK → phase ACTIVATE"),
    phase(2, "activate", "software-activate", "ACTIVATE_OK → COMPLETED"),
  ]);
}
