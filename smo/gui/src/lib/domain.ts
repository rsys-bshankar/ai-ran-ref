// Pure domain helpers shared by pages: state machines as the modules define
// them, alarm severity handling, and turning reports into chart series.

import type { Alarm } from "../api/types";

// ---------------------------------------------------------------- AI/ML model FSM
// aimgf/app/statemachine.py

export const MODEL_PIPELINE = ["REGISTERED", "TRAINING", "TESTED", "EMULATED", "CERTIFIED", "LOADED", "ACTIVE"] as const;

export type ModelAction =
  | { kind: "train"; label: string }           // POST /training-jobs (fires TRAIN or RETRAIN itself)
  | { kind: "advance"; event: string; label: string };

/** The operator actions legal from a model state. TRAIN/RETRAIN go through
 * RequestTraining, not a bare advance, so a TrainingJob row exists for them. */
export function modelActions(state: string): ModelAction[] {
  switch (state) {
    case "REGISTERED": return [{ kind: "train", label: "Request training" }];
    case "TRAINING": return [{ kind: "advance", event: "TRAINING_COMPLETE", label: "Training complete" }];
    case "TESTED": return [{ kind: "advance", event: "VALIDATION_COMPLETE", label: "Validation complete" }];
    case "EMULATED": return [{ kind: "advance", event: "CERTIFY", label: "Certify" }];
    case "CERTIFIED": return [{ kind: "advance", event: "LOAD", label: "Load" }];
    case "LOADED": return [{ kind: "advance", event: "ACTIVATE", label: "Activate" }];
    case "ACTIVE": return [{ kind: "train", label: "Retrain" }, { kind: "advance", event: "DEPRECATE", label: "Deprecate" }];
    default: return [];
  }
}

export type StepStatus = "done" | "current" | "todo";

export function pipelineSteps(state: string): { state: string; status: StepStatus }[] {
  const idx = MODEL_PIPELINE.indexOf(state as (typeof MODEL_PIPELINE)[number]);
  if (state === "DEPRECATED") return MODEL_PIPELINE.map((s) => ({ state: s, status: "done" as StepStatus }));
  return MODEL_PIPELINE.map((s, i) => ({ state: s, status: i < idx ? "done" : i === idx ? "current" : "todo" }));
}

export const DEPLOYABLE_MODEL_STATES = ["CERTIFIED", "LOADED", "ACTIVE"];

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
