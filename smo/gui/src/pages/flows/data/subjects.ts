/** Subject helpers shared by every flow board: the size of a subject list, the default choice, and the last five subjects an operator
 * followed (kept per browser in localStorage, read and written under try/catch: a private window or blocked storage just has no history). */
import type { Subject } from "./types";

/** How many subjects a picker loads (the backend's page maximum, SCALE.md P1); the README notes the cut. */
export const SUBJECT_LIMIT = 500;
/** How many recent subjects are remembered per flow. */
export const RECENT_MAX = 5;
const KEY = "smo.flows.recent";

/** The subject with `id`, or the newest (lists are oldest first, so the last) when `id` is null or unknown. */
export function choose<T>(items: T[] | undefined, id: string | null, key: (t: T) => string): T | undefined {
  if (!items?.length) return undefined;
  return (id ? items.find((t) => key(t) === id) : undefined) ?? items[items.length - 1];
}

/** The recent subject ids of one flow, newest first. */
export function readRecent(flowId: string): string[] {
  try {
    const all = JSON.parse(window.localStorage.getItem(KEY) ?? "{}") as Record<string, unknown>;
    const list = all[flowId];
    return Array.isArray(list) ? list.filter((x): x is string => typeof x === "string").slice(0, RECENT_MAX) : [];
  } catch {
    return [];
  }
}

/** Puts `subjectId` first in the recent list of `flowId` (deduplicated, at most RECENT_MAX). */
export function rememberRecent(flowId: string, subjectId: string): void {
  try {
    const all = JSON.parse(window.localStorage.getItem(KEY) ?? "{}") as Record<string, string[]>;
    all[flowId] = [subjectId, ...(all[flowId] ?? []).filter((x) => x !== subjectId)].slice(0, RECENT_MAX);
    window.localStorage.setItem(KEY, JSON.stringify(all));
  } catch {
    /* storage unavailable: no history, nothing else changes */
  }
}

/** Subjects whose label or id contains `text` (case-insensitive), recent ones first, at most `max`. */
export function filterSubjects(subjects: Subject[], text: string, recent: string[], max = 20): Subject[] {
  const t = text.trim().toLowerCase();
  const hit = subjects.filter((s) => !t || s.label.toLowerCase().includes(t) || s.id.toLowerCase().includes(t));
  const rank = (s: Subject) => { const i = recent.indexOf(s.id); return i < 0 ? RECENT_MAX : i; };
  return [...hit].sort((a, b) => rank(a) - rank(b)).slice(0, max);
}
