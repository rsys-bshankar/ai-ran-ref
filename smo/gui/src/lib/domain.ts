// Pure domain helpers shared by pages: state machines as the modules define
// them, alarm severity handling, and turning reports into chart series.

import type { Alarm } from "../api/types";

// ---------------------------------------------------------------- AI/ML model FSMs
// aimgf/app/statemachine.py — Wave 2 split ModelLifecycle (a model's own
// identity/certification path) from RuntimeLifecycle (its serving
// existence), replacing the flat single pipeline this GUI drew before.

export const MODEL_PIPELINE = [
  "REGISTERED", "TRAINING", "TRAINED", "VALIDATING", "VALIDATED", "EMULATING", "EMULATED",
  "PENDING_APPROVAL", "APPROVED", "CERTIFIED", "PROMOTED",
] as const;

export type ModelAction =
  | { kind: "train"; label: string }           // POST /training-jobs (fires CREATE_TRAINING itself)
  | { kind: "validate"; label: string }        // POST /validation-jobs (fires CREATE_VALIDATION itself)
  | { kind: "emulate"; label: string }         // POST /emulation-jobs (fires CREATE_EMULATION itself)
  | { kind: "complete"; stage: CompletionStage; label: string }  // POST /<stage>-jobs/{id}/complete (fires …_COMPLETE itself)
  | { kind: "advance"; event: string; label: string; governance?: boolean };  // governance: decidedBy required

export type CompletionStage = "training" | "validation" | "emulation";

/** Where a stage's in-flight job lives: AIMgF's `advance` refuses the
 * job-driven events (TRAINING_COMPLETE etc.), so a run completes through
 * its own job's `/complete` route. Training's in-flight status is
 * IN_PROGRESS (or SUSPENDED); validation/emulation use RUNNING. */
export function completionRoute(stage: CompletionStage): { jobsPath: string; runningStatus: string; idKey: string } {
  switch (stage) {
    case "training": return { jobsPath: "/aimgf/training-jobs", runningStatus: "IN_PROGRESS", idKey: "trainingJobId" };
    case "validation": return { jobsPath: "/aimgf/validation-jobs", runningStatus: "RUNNING", idKey: "validationJobId" };
    case "emulation": return { jobsPath: "/aimgf/emulation-jobs", runningStatus: "RUNNING", idKey: "emulationJobId" };
  }
}

/** The operator actions legal from a ModelLifecycleState. TRAIN/VALIDATE/
 * EMULATE and their completions go through their own job routes, not a
 * bare advance (AIMgF refuses job-driven events there), so a
 * TrainingJob/ValidationJob/EmulationJob row exists for each. `governance`
 * actions are the eight decisions AIMgF requires a decidedBy for
 * (SUBMIT_FOR_APPROVAL/APPROVE/REJECT/CERTIFY/PROMOTE/ROLLBACK, plus
 * HISTORY.md OI-6.1's own APPROVE_TRAINING/APPROVE_VALIDATION
 * operator gate) — DEPRECATE/RETIRE aren't governance in
 * docs/ARCHITECTURE.md's AIMgF sense.
 *
 * `gate` reflects ModelLifecycle.trainingApproved/validationApproved —
 * TRAINED/VALIDATED only offer the next request route once an operator
 * has already approved the stage that just finished; omitting it (e.g. a
 * caller with no lifecycle row yet) defaults to "not yet approved". */
export function modelActions(state: string, gate?: { trainingApproved: boolean; validationApproved: boolean }): ModelAction[] {
  switch (state) {
    case "REGISTERED": return [{ kind: "train", label: "Request training" }];
    case "TRAINING": return [{ kind: "complete", stage: "training", label: "Training complete" }];
    case "TRAINED":
      return gate?.trainingApproved
        ? [{ kind: "validate", label: "Request validation" }]
        : [{ kind: "advance", event: "APPROVE_TRAINING", label: "Approve training", governance: true }];
    case "VALIDATING": return [{ kind: "complete", stage: "validation", label: "Validation complete" }];
    case "VALIDATED":
      return gate?.validationApproved
        ? [{ kind: "emulate", label: "Request emulation" }]
        : [{ kind: "advance", event: "APPROVE_VALIDATION", label: "Approve validation", governance: true }];
    case "EMULATING": return [{ kind: "complete", stage: "emulation", label: "Emulation complete" }];
    case "EMULATED": return [{ kind: "advance", event: "SUBMIT_FOR_APPROVAL", label: "Submit for approval", governance: true }];
    case "PENDING_APPROVAL": return [
      { kind: "advance", event: "APPROVE", label: "Approve", governance: true },
      { kind: "advance", event: "REJECT", label: "Reject", governance: true },
    ];
    case "APPROVED": return [{ kind: "advance", event: "CERTIFY", label: "Certify", governance: true }];
    case "CERTIFIED": return [
      { kind: "advance", event: "PROMOTE", label: "Promote", governance: true },
      { kind: "train", label: "Retrain" },  // e.g. after a ROLLBACK
      { kind: "advance", event: "DEPRECATE", label: "Deprecate" },
    ];
    case "PROMOTED": return [
      { kind: "train", label: "Retrain" },
      { kind: "advance", event: "ROLLBACK", label: "Rollback", governance: true },
      { kind: "advance", event: "DEPRECATE", label: "Deprecate" },
    ];
    case "DEPRECATED": return [{ kind: "advance", event: "RETIRE", label: "Retire" }];
    case "FAILED": return [{ kind: "train", label: "Retry training" }, { kind: "advance", event: "RETIRE", label: "Retire" }];
    default: return [];
  }
}

export type StepStatus = "done" | "current" | "todo";

export function pipelineSteps(state: string): { state: string; status: StepStatus }[] {
  const idx = MODEL_PIPELINE.indexOf(state as (typeof MODEL_PIPELINE)[number]);
  if (state === "DEPRECATED" || state === "RETIRED" || state === "FAILED") {
    return MODEL_PIPELINE.map((s) => ({ state: s, status: "done" as StepStatus }));
  }
  return MODEL_PIPELINE.map((s, i) => ({ state: s, status: i < idx ? "done" : i === idx ? "current" : "todo" }));
}

// A model can only be deployed (a RuntimeLifecycle can only be requested)
// once its own ModelLifecycle has cleared governance.
export const DEPLOYABLE_MODEL_STATES = ["CERTIFIED", "PROMOTED"];

// The "progress" ordering for a runtime — SCALING/TERMINATING/TERMINATED
// are transient/terminal, excluded the same way DEPRECATED/RETIRED/FAILED
// are from MODEL_PIPELINE above.
export const RUNTIME_PIPELINE = ["NOT_DEPLOYED", "DEPLOYMENT_REQUESTED", "DEPLOYED", "ACTIVATING", "ACTIVE"] as const;

/** The operator actions legal from a RuntimeLifecycleState — jointly owned
 * with NFO (docs/ARCHITECTURE.md's AIMgF "NFO invocation" list). */
export function runtimeActions(state: string): { action: "deploy" | "activate" | "scale" | "terminate"; label: string }[] {
  switch (state) {
    case "NOT_DEPLOYED": return [{ action: "deploy", label: "Deploy runtime" }];
    case "DEPLOYMENT_REQUESTED": return [{ action: "terminate", label: "Terminate" }];
    case "DEPLOYED": return [{ action: "activate", label: "Activate" }, { action: "terminate", label: "Terminate" }];
    case "ACTIVE": return [{ action: "scale", label: "Scale" }, { action: "terminate", label: "Terminate" }];
    default: return [];
  }
}

// ---------------------------------------------------------------- packages / instances

/** onboarding/app/statemachine.py — which lifecycle calls a package state allows. */
export function packageActions(state: string): { action: "prime" | "deprime" | "deprecate" | "cancel-delete" | "delete"; label: string }[] {
  switch (state) {
    case "AVAILABLE": return [{ action: "prime", label: "Prime" }, { action: "deprecate", label: "Deprecate" }, { action: "delete", label: "Delete" }];
    case "PRIMED": return [{ action: "deprime", label: "Deprime" }];
    case "DEPRECATED": return [{ action: "cancel-delete", label: "Restore" }, { action: "delete", label: "Delete" }];
    case "FAILED": return [{ action: "delete", label: "Delete" }];
    default: return [];
  }
}

// ---------------------------------------------------------------- A1 service supervision

/** Seconds until a supervised A1 service's keep-alive lapses (a1-related's
 * lazy sweep then deregisters it, and its policies, on the next registry
 * read); null when the service isn't supervised (interval 0). */
export function keepAliveRemaining(s: { keepAliveIntervalSeconds: number; timeSinceLastActivitySeconds?: number }): number | null {
  if (!s.keepAliveIntervalSeconds) return null;
  return Math.max(0, s.keepAliveIntervalSeconds - (s.timeSinceLastActivitySeconds ?? 0));
}

// ---------------------------------------------------------------- alarms

export const SEVERITIES = ["critical", "major", "minor", "warning"] as const;
export type Severity = (typeof SEVERITIES)[number] | "cleared";

export function countBySeverity(alarms: Pick<Alarm, "severity">[]): Record<(typeof SEVERITIES)[number], number> {
  const counts = { critical: 0, major: 0, minor: 0, warning: 0 };
  for (const a of alarms) {
    const s = a.severity?.toLowerCase();
    if (s in counts) counts[s as keyof typeof counts] += 1;
  }
  return counts;
}

export function severityRank(severity: string): number {
  const i = (SEVERITIES as readonly string[]).indexOf(severity?.toLowerCase());
  return i === -1 ? SEVERITIES.length : i;
}

/** Open alarms first, most severe first, newest first. */
export function sortAlarms<T extends Pick<Alarm, "severity" | "raisedAt">>(alarms: T[]): T[] {
  return [...alarms].sort((a, b) => severityRank(a.severity) - severityRank(b.severity) || (b.raisedAt ?? "").localeCompare(a.raisedAt ?? ""));
}

// ---------------------------------------------------------------- KPI series

export interface Reported { metrics: Record<string, unknown>; reportedAt: string }

/** Every numeric metric key across a set of reports. */
export function numericMetricKeys(reports: Reported[]): string[] {
  const keys = new Set<string>();
  for (const r of reports) for (const [k, v] of Object.entries(r.metrics ?? {})) if (typeof v === "number" && Number.isFinite(v)) keys.add(k);
  return [...keys].sort();
}

/** One metric over time, oldest first (the list endpoints return newest first). */
export function metricSeries(reports: Reported[], key: string): { t: string; v: number }[] {
  return reports
    .filter((r) => typeof r.metrics?.[key] === "number")
    .map((r) => ({ t: r.reportedAt, v: r.metrics[key] as number }))
    .sort((a, b) => a.t.localeCompare(b.t));
}

// ---------------------------------------------------------------- formatting

export function shortId(id: string | null | undefined): string {
  if (!id) return "—";
  return id.length > 13 ? `${id.slice(0, 8)}…` : id;
}

export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { dateStyle: "short", timeStyle: "medium" });
}

export function parseJsonObject(text: string): { ok: true; value: Record<string, unknown> } | { ok: false; error: string } {
  if (!text.trim()) return { ok: true, value: {} };
  try {
    const v = JSON.parse(text);
    if (v === null || typeof v !== "object" || Array.isArray(v)) return { ok: false, error: "must be a JSON object" };
    return { ok: true, value: v };
  } catch (e) {
    return { ok: false, error: (e as Error).message };
  }
}

export function splitList(text: string): string[] {
  return text.split(/[,\n]/).map((s) => s.trim()).filter(Boolean);
}

// ---------------------------------------------------------------- rApp safeguards (AI-10.x)

export const REFUSAL_CODES = ["RAPP_KILLED", "RAPP_RATE_LIMITED", "RAPP_BLAST_RADIUS_EXCEEDED", "RAPP_MAGNITUDE_EXCEEDED"] as const;

/** What a refusal code means, in the operator's words. */
export const REFUSAL_MEANING: Record<(typeof REFUSAL_CODES)[number], string> = {
  RAPP_KILLED: "The rApp was stopped by an operator",
  RAPP_RATE_LIMITED: "It started too many config jobs in the last hour",
  RAPP_BLAST_RADIUS_EXCEEDED: "One job touched more managed elements than allowed",
  RAPP_MAGNITUDE_EXCEEDED: "A value moved further than allowed in one write",
};

export interface LimitsLike {
  maxConfigJobsPerHour: number | null; maxElementsPerJob: number | null; maxChangePercent: number | null; configJobsLastHour?: number;
}

/** One line for a limits row: "20/h (3 used) · ≤5 elements · ≤10%"; "No limits" when none is set. */
export function describeLimits(limits: LimitsLike | null | undefined): string {
  if (!limits) return "No limits";
  const parts: string[] = [];
  if (limits.maxConfigJobsPerHour != null) {
    parts.push(`${limits.maxConfigJobsPerHour}/h${limits.configJobsLastHour != null ? ` (${limits.configJobsLastHour} used)` : ""}`);
  }
  if (limits.maxElementsPerJob != null) parts.push(`≤${limits.maxElementsPerJob} elements`);
  if (limits.maxChangePercent != null) parts.push(`≤${limits.maxChangePercent}%`);
  return parts.length ? parts.join(" · ") : "No limits";
}

export interface LimitsForm { jobsPerHour: string; elementsPerJob: string; changePercent: string }

/** The body of PUT /ran-nf-oam/rapp-limits/{id} from the three form fields, or the problem to show. A blank field is "no such limit"; the
 * bounds are the ones RAN NF OAM enforces (it replaces the whole set, so a blank removes that limit). */
export function limitsPayload(form: LimitsForm): { ok: true; body: Record<string, number> } | { ok: false; error: string } {
  const body: Record<string, number> = {};
  const read = (text: string, name: string, key: string, integer: boolean, min: number, max: number): string | null => {
    const raw = text.trim();
    if (raw === "") return null;
    const n = Number(raw);
    if (!Number.isFinite(n) || (integer && !Number.isInteger(n))) return `${name} must be ${integer ? "a whole number" : "a number"}`;
    if (n < min || n > max) return `${name} must be between ${min} and ${max}`;
    body[key] = n;
    return null;
  };
  const problem = read(form.jobsPerHour, "Config jobs per hour", "maxConfigJobsPerHour", true, 1, 100_000)
    ?? read(form.elementsPerJob, "Elements per job", "maxElementsPerJob", true, 1, 10_000)
    ?? read(form.changePercent, "Change percent", "maxChangePercent", false, 0.0001, 10_000);
  if (problem) return { ok: false, error: problem };
  if (Object.keys(body).length === 0) return { ok: false, error: "Set at least one limit (to remove them all, use Remove limits)" };
  return { ok: true, body };
}

/** The form's starting values from the limits in force. */
export function limitsForm(limits: LimitsLike | null | undefined): LimitsForm {
  const text = (n: number | null | undefined) => (n == null ? "" : String(n));
  return { jobsPerHour: text(limits?.maxConfigJobsPerHour), elementsPerJob: text(limits?.maxElementsPerJob), changePercent: text(limits?.maxChangePercent) };
}
