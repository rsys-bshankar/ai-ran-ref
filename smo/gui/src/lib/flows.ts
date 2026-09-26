// The ten end-to-end journeys in smo/docs/call-flows, as pure functions from
// live SMO state to step status. The Lifecycle page renders these; keeping
// them pure keeps "is this step done?" testable without a browser.
//
// A step is `done` when the entity state proves it happened, `current` when it
// is the next thing to do, `failed` when the state shows it went wrong, and
// `todo` when it is further ahead. `blocked` marks a step an earlier failure
// makes impossible.

import type {
  AnalyticsProducer, AnalyticsReport, AnalyticsSubscription, ConfigJob, DataJob, DataOffer, DmeType, EiType, FaultReport,
  InferenceJob, Instance, Intent, IntentReport, MlmfReport, MlmfSubscription, Model, Monitor, NfDeployment, O1Endpoint,
  Package, PackageUsage, PerfReport, RemedialAction, Rmih, ServiceOrder, TrainingJob,
} from "../api/types";
import { MODEL_PIPELINE } from "./domain";

export type StepStatus = "done" | "current" | "todo" | "failed" | "blocked" | "warn";

export interface FlowStep {
  id: string;
  title: string;
  actor: string;          // who performs it (Operator, Onboarding, NFO, rApp container, ...)
  status: StepStatus;
  detail?: string;
}

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
  { id: "02", number: "02", title: "AI/ML model: register → train → certify → deploy → infer → monitor", doc: "02-aiml-model-train-to-inference.md", subject: "model", modules: ["ai-ml-workflow", "dme"] },
  { id: "03", number: "03", title: "Configuration write, schema-checked, fleet-aware", doc: "03-config-write-with-schema-check.md", subject: "config job", modules: ["ran-nf-oam"] },
  { id: "04", number: "04", title: "Closed-loop assurance: monitor → decide → remediate → escalate", doc: "04-closed-loop-assurance.md", subject: "assurance monitor", modules: ["so-smos", "sa-smos", "ran-analytics", "ran-nf-oam", "nfo"] },
  { id: "05", number: "05", title: "A1 EI registration → data consumption", doc: "05-a1-ei-registration-to-consumption.md", subject: "EI type", modules: ["a1-related", "dme"] },
  { id: "06", number: "06", title: "Package failure, deprecation and the cascade-delete guard", doc: "06-onboarding-failure-deprecation-deletion.md", subject: "package", modules: ["onboarding", "rapp-mgmt"] },
  { id: "07", number: "07", title: "rApp fault and performance reporting", doc: "07-rapp-fault-and-performance-reporting.md", subject: "rApp instance", modules: ["rapp-mgmt"] },
  { id: "08", number: "08", title: "RAN Analytics: producer → report → subscriber query", doc: "08-ran-analytics-data-production.md", subject: "analytics type", modules: ["ran-analytics", "sme"] },
  { id: "09", number: "09", title: "Intent registration → fulfilment reporting → admin state", doc: "09-policy-mgmt-intent-flow.md", subject: "intent", modules: ["policy-mgmt"] },
  { id: "10", number: "10", title: "SO SMOS multi-step order: INFRA → TRAINING → DEPLOY", doc: "10-so-smos-multi-step-infra-training-deploy.md", subject: "service order", modules: ["so-smos", "focom", "ai-ml-workflow", "nfo"] },
];

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

export function progress(steps: FlowStep[]): { done: number; total: number; failed: boolean; complete: boolean } {
  const done = steps.filter((s) => s.status === "done" || s.status === "warn").length;
  return { done, total: steps.length, failed: steps.some((s) => s.status === "failed"), complete: done === steps.length };
}

const step = (id: string, title: string, actor: string, ok: boolean | "failed" | "warn", detail?: string): FlowStep =>
  ({ id, title, actor, status: ok === "failed" ? "failed" : ok === "warn" ? "warn" : ok ? "done" : "todo", detail });

const PKG_ONBOARDED = ["AVAILABLE", "PRIMING", "PRIMED", "DEPRIMING", "DEPRECATED", "DELETING"];

// ---------------------------------------------------------------- 01

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

const reached = (state: string, target: string) => {
  if (state === "DEPRECATED") return true;
  return MODEL_PIPELINE.indexOf(state as (typeof MODEL_PIPELINE)[number]) >= MODEL_PIPELINE.indexOf(target as (typeof MODEL_PIPELINE)[number]);
};

export function flow02(model: Model | undefined, jobs: TrainingJob[], inference: InferenceJob[], subs: MlmfSubscription[], reports: MlmfReport[]): FlowStep[] {
  if (!model) return settle([step("register", "RegisterModel(modelType, version)", "Producer → AI/ML", false)]);
  const s = model.state;
  const inferenceDone = inference.find((j) => j.status === "COMPLETED");
  const inferenceFailed = inference.length > 0 && inference.every((j) => j.status === "FAILED");
  const breached = reports.filter((r) => r.breachedFloor).length;
  return settle([
    step("register", "RegisterModel(modelType, version)", "Producer → AI/ML", true, `${model.modelType} v${model.version}`),
    step("train", "RequestTraining → TRAINING", "Producer → AI/ML (MLTF)", jobs.length > 0 || reached(s, "TRAINING"), jobs.length ? `${jobs.length} training job(s)` : undefined),
    step("tested", "advance(TRAINING_COMPLETE) → TESTED", "MLVF", reached(s, "TESTED")),
    step("emulated", "advance(VALIDATION_COMPLETE) → EMULATED", "MLEF", reached(s, "EMULATED")),
    step("certified", "advance(CERTIFY) → CERTIFIED (AIMgF gate)", "AIMgF", reached(s, "CERTIFIED")),
    step("deploy", "RequestModelDeployment(nodeGroups)", "Producer → MLLF", model.clearedNodeGroups.length > 0, model.clearedNodeGroups.join(", ") || undefined),
    step("loaded", "advance(LOAD) → LOADED", "MLLF", reached(s, "LOADED")),
    step("active", "advance(ACTIVATE) → ACTIVE", "MLLF", reached(s, "ACTIVE")),
    step("infer", "RequestInference → COMPLETED (result via DME)", "Consumer → MLEF",
      inferenceDone ? true : inferenceFailed ? "warn" : false, inference.length ? `${inference.length} job(s): ${inference.map((j) => j.status).join(", ")}` : undefined),
    step("monitor", "SubscribePerformanceMonitoring(guardKpiFloor)", "Producer → MLMF", subs.length > 0, subs.length ? `${subs.length} subscription(s)` : undefined),
    step("report", "ReportPerformance → breachedFloor → retrain", "Producer → MLMF", reports.length === 0 ? false : breached ? "warn" : true,
      reports.length ? `${reports.length} report(s), ${breached} under the floor${breached ? " (retrain triggered)" : ""}` : undefined),
  ]);
}

// ---------------------------------------------------------------- 03

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

// ---------------------------------------------------------------- 05

export function flow05(ei: EiType | undefined, dmeType: DmeType | undefined, offers: DataOffer[], jobs: DataJob[]): FlowStep[] {
  const committed = offers.find((o) => o.committedMethod);
  const active = jobs.filter((j) => j.status === "ACTIVE" || j.status === "PENDING");
  return settle([
    step("register", "RegisterEIType(eiTypeId, dme namespace/name/version)", "Producer → A1 Related", !!ei, ei ? `${ei.eiTypeId} by ${ei.registeredBy}` : undefined),
    step("dme-type", "Wraps DME RegisterDMEType (eiSourceDmeTypeId)", "A1 Related → DME", !!dmeType,
      dmeType ? `${dmeType.typeName} (${dmeType.typeStatus})` : ei ? "DME type no longer registered" : undefined),
    step("offer", "Producer DataOffer(dataDeliveryMethods)", "Producer → DME", offers.length > 0, offers.length ? `${offers.length} offer(s)` : undefined),
    step("commit", "DME commits one delivery method", "DME", !!committed, committed?.committedMethod ?? undefined),
    step("job", "Consumer CreateDataJob(PULL_HTTP / PUSH / STREAMING)", "Consumer → DME", jobs.length > 0,
      jobs.length ? jobs.map((j) => `${j.consumerId}: ${j.dataDeliveryMethod} ${j.status}`).join(", ") : undefined),
    step("consume", "Consumer pulls / receives EI data", "DME → Consumer", active.length > 0 ? true : jobs.length ? "warn" : false,
      jobs.length && !active.length ? "all jobs terminated" : undefined),
  ]);
}

// ---------------------------------------------------------------- 06

export function flow06(pkg: Package | undefined, usage: PackageUsage[], instanceCount: number): FlowStep[] {
  if (!pkg) return settle([step("onboard", "OnboardPackage", "Operator → Onboarding", false)]);
  const active = usage.filter((u) => u.active).length;
  const failed = pkg.state === "FAILED";
  return settle([
    step("onboard", "OnboardPackage → AVAILABLE or FAILED", "Onboarding", failed ? "warn" : PKG_ONBOARDED.includes(pkg.state), `state ${pkg.state}`),
    step("refuse", failed ? "CreateInstance refused (409: never AVAILABLE)" : "Package usable: instances may reference it", "rApp Mgmt",
      failed ? true : pkg.state !== "ONBOARDING", failed ? undefined : `${instanceCount} instance(s)`),
    step("deprecate", "Deprecate (AVAILABLE → DEPRECATED)", "Operator → Onboarding",
      failed || ["DEPRECATED", "DELETING"].includes(pkg.state)),
    step("guard", "Cascade-delete guard: no active usage registrations", "Onboarding",
      // active usage is a hard stop for delete (and deprime): shown as the
      // failing step so the delete step reads as blocked, not next
      failed ? true : active === 0 ? usage.length > 0 || pkg.state === "DELETING" : "failed",
      `${active} active / ${usage.length} usage registration(s)${active ? " — delete is blocked" : ""}`),
    step("delete", "DeletePackage → DELETING", "Operator → Onboarding", pkg.state === "DELETING"),
  ]);
}

// ---------------------------------------------------------------- 07

export function flow07(instance: Instance | undefined, perf: PerfReport[], faults: FaultReport[]): FlowStep[] {
  if (!instance) return settle([step("running", "Instance RUNNING (call flow 01)", "rApp Mgmt", false)]);
  const critical = faults.filter((f) => f.severity === "critical");
  const minor = faults.filter((f) => f.severity !== "critical");
  const s = instance.state;
  return settle([
    step("running", "Instance RUNNING (call flow 01)", "rApp Mgmt", s !== "DEPLOYING" || perf.length + faults.length > 0, `state ${s}`),
    step("perf", "ReportPerformance(metrics) — no state change", "rApp container → R1 → rApp Mgmt", perf.length > 0, perf.length ? `${perf.length} report(s)` : undefined),
    step("minor", "ReportFault(non-critical) — recorded only", "rApp container → rApp Mgmt", minor.length > 0, minor.length ? `${minor.length} fault(s)` : undefined),
    step("crash", "ReportFault(critical) → FAULTED (CRASH)", "rApp container → rApp Mgmt", critical.length > 0 ? (s === "FAULTED" ? "failed" : true) : false,
      critical.length ? `${critical.length} critical fault(s)` : undefined),
    step("recover", "RECOVER → DEPLOYING → re-bootstrap → RUNNING", "Operator → rApp Mgmt",
      critical.length > 0 && s === "RUNNING" ? true : critical.length > 0 && s === "DEPLOYING" ? "warn" : false,
      critical.length && s === "DEPLOYING" ? "awaiting re-bootstrap" : undefined),
  ]);
}

// ---------------------------------------------------------------- 08

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

export function flow09(handlers: Rmih[], intent: Intent | undefined, reports: IntentReport[]): FlowStep[] {
  return settle([
    step("rmih", "RegisterIntentHandlingFunction (framework-internal only)", "SO/SA SMOS → Policy Mgmt", handlers.length > 0,
      handlers.length ? handlers.map((h) => h.rmihId).join(", ") : undefined),
    step("create", "CreateIntent(expectations, priority)", "RMIO → Policy Mgmt", !!intent, intent ? `priority ${intent.intentPriority}, RMIO ${intent.rmioId}` : undefined),
    step("dispatch", "Matching RMIHs notified (expectationObject.objectType)", "Policy Mgmt → RMIH", !!intent && handlers.length > 0 ? true : intent ? "warn" : false,
      intent && !handlers.length ? "no handler registered to receive it" : undefined),
    step("report", "PublishIntentReport(fulfilment, conflicts)", "RMIH → Policy Mgmt", reports.length > 0, reports.length ? `${reports.length} report(s)` : undefined),
    step("admin", "UpdateIntentAdminState (RMIO only)", "RMIO → Policy Mgmt", intent?.intentAdminState === "DEACTIVATED", intent ? `state ${intent.intentAdminState}` : undefined),
  ]);
}

// ---------------------------------------------------------------- 10

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
