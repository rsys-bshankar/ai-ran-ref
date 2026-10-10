/**
 * The pure part of the generic renderer for the operator page a rApp declares (PR-GUI-8, GUI-8.4; docs/adr/0004-operator-ui-declaration.md): reading
 * fields by the path subset, filling route parameters, formatting values, building chart series, checking an action's inputs. No React, no network.
 *
 * Everything here is defensive on purpose. The declaration was validated at onboarding, but the stored one may come from a newer Onboarding than this
 * GUI build, or have been edited in the database, and the answers of a rApp are arbitrary: a missing field, a wrong type or a prototype name ("constructor")
 * shows "—" or text, and nothing throws. Values are always text: nothing here makes HTML, a link, a style or an attribute out of data.
 *
 * The route-filling functions are a security boundary: a value from a row reaches a URL path or query only when it is one safe segment (`safeSegment`), so a row cannot
 * steer a call to another route. Used by `components/OperatorUi.tsx` and `api/rapps.ts`; covered by `operatorUi.test.ts`.
 */

import { formatTime } from "./domain";

export type Obj = Record<string, unknown>;

export const MAX_SERIES = 8;
export const SAFE_SEGMENT = /^[A-Za-z0-9._~-]+$/;
const ROW_REF = /\{row\.([A-Za-z_][A-Za-z0-9_]*)\}/g;
const WHOLE_ROW_REF = /^\{row\.([A-Za-z_][A-Za-z0-9_]*)\}$/;

export function isObj(value: unknown): value is Obj {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

const own = (o: Obj, key: string) => Object.prototype.hasOwnProperty.call(o, key);

// ---------------------------------------------------------------- field paths: dotted names, and at most one `[]` that walks a list

/** The value at `path` of `value`, or undefined. `a.b` walks objects; `datasets[].dataset` walks every element of the list `datasets` and returns the list of what it found.
 * Only a name the object itself has is followed (never `constructor` or `__proto__`). */
export function getPath(value: unknown, path: string): unknown {
  const at = path.indexOf("[]");
  if (at >= 0) {
    const list = getPath(value, path.slice(0, at));
    if (!Array.isArray(list)) return undefined;
    const rest = path.slice(at + 2).replace(/^\./, "");
    return rest ? list.map((item) => getPath(item, rest)) : list;
  }
  let current: unknown = value;
  for (const name of path.split(".")) {
    if (name === "") continue;
    if (!isObj(current) || !own(current, name)) return undefined;
    current = current[name];
  }
  return current;
}

/** The list a `rows` / `points` path names; the empty path is the answer itself. Undefined when it is not a list. */
export function listAt(value: unknown, path: string | undefined): unknown[] | undefined {
  const found = path ? getPath(value, path) : value;
  return Array.isArray(found) ? found : undefined;
}

// ---------------------------------------------------------------- routes

/** One safe path segment out of a row's value, or null (letters, digits, `._~-`; not `.` or `..`). */
export function safeSegment(value: unknown): string | null {
  if (typeof value !== "string" && typeof value !== "number") return null;
  const text = String(value);
  return SAFE_SEGMENT.test(text) && text !== "." && text !== ".." ? text : null;
}

/** The route with `{instanceId}` and `{row.<field>}` filled. Null when a value is not one safe segment (the call is then not sent). */
export function fillRoute(template: string, instanceId: string, row?: Obj): string | null {
  const id = safeSegment(instanceId);
  if (id === null) return null;
  let failed = false;
  const path = template.replace(/\{instanceId\}/g, id).replace(ROW_REF, (_all, field: string) => {
    const segment = row && own(row, field) ? safeSegment(row[field]) : null;
    if (segment === null) failed = true;
    return segment ?? "";
  });
  return failed || path.includes("{") ? null : path;
}

/** The query of a source: fixed values as declared, a whole-value `{row.<field>}` from the row. Null when a row value is not one safe segment. */
export function fillQuery(query: Obj | undefined, row?: Obj): Record<string, string | number | boolean> | null {
  const out: Record<string, string | number | boolean> = {};
  for (const [name, value] of Object.entries(query ?? {})) {
    if (typeof value === "string") {
      const ref = WHOLE_ROW_REF.exec(value);
      if (ref) {
        const segment = row && own(row, ref[1]) ? safeSegment(row[ref[1]]) : null;
        if (segment === null) return null;
        out[name] = segment;
        continue;
      }
    }
    if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") out[name] = value;
  }
  return out;
}

/** A title with `{row.<field>}` replaced by the row's text (plain text; a missing field is "—"). */
export function fillTitle(title: string, row: Obj | undefined): string {
  return title.replace(ROW_REF, (_all, field: string) => (row && own(row, field) ? formatValue(row[field]) : "—"));
}

/** The `when` of a row action: `{path, exists}`, `{path, equals}` or `{path, notEquals}` on the row. A `when` this GUI does not understand shows the button. */
export function whenMatches(when: unknown, row: Obj): boolean {
  if (!isObj(when) || typeof when.path !== "string") return true;
  const found = getPath(row, when.path);
  const present = found !== undefined && found !== null && found !== "";
  if (typeof when.exists === "boolean") return present === when.exists;
  if ("equals" in when) return found === when.equals;
  if ("notEquals" in when) return found !== when.notEquals;
  return true;
}

// ---------------------------------------------------------------- values

export const FORMATS = ["text", "number", "percent", "datetime", "badge", "id", "boolean", "list", "sparkline"] as const;

/** Text of a number the way the other pages show them: no decimals from 100 up, else three significant digits. */
export function formatNumber(n: number): string {
  if (!Number.isFinite(n)) return String(n);
  return Math.abs(n) >= 100 ? n.toFixed(0) : String(Number(n.toPrecision(3)));
}

/** Compact text for any value that is not shown as a structure: objects and lists become short JSON, so a value that does not fit its format is still text. */
export function asText(value: unknown): string {
  if (value === undefined || value === null || value === "") return "—";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  const json = JSON.stringify(value);
  return json.length > 200 ? `${json.slice(0, 197)}…` : json;
}

/** The text of a value in a declared format (`badge` and `id` are text here; the component styles them). A value that does not fit the format is shown as text. */
export function formatValue(value: unknown, format?: string, unit?: string): string {
  if (value === undefined || value === null || value === "") return "—";
  const withUnit = (text: string) => (unit ? `${text} ${unit}` : text);
  switch (format) {
    case "number": return typeof value === "number" ? withUnit(formatNumber(value)) : asText(value);
    case "percent": return typeof value === "number" ? `${formatNumber(value)} %` : asText(value);
    case "datetime": return typeof value === "string" || typeof value === "number" ? formatTime(typeof value === "number" ? new Date(value).toISOString() : value) : asText(value);
    case "boolean": return typeof value === "boolean" ? (value ? "yes" : "no") : asText(value);
    case "list": return Array.isArray(value) ? (value.length ? value.map(asText).join(", ") : "—") : asText(value);
    default: return unit && (typeof value === "number") ? withUnit(formatNumber(value)) : asText(value);
  }
}

/**
 * The finite number a value stands for: a finite number as it is, a non-blank numeric string converted; anything else (NaN, infinity, text, null) is null.
 */
export function numberOf(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "" && Number.isFinite(Number(value))) return Number(value);
  return null;
}

// ---------------------------------------------------------------- series for charts and sparklines

export interface Point { x: string; y: number }
export interface Series { name: string; points: Point[] }

/** One series per `seriesBy` value (none: one series), at most MAX_SERIES drawn; a point whose x is missing or whose y is not a number is left out.
 * `extra` is how many series were not drawn. */
export function toSeries(points: unknown, x: string, y: string, seriesBy?: string): { series: Series[]; extra: number } {
  const list = Array.isArray(points) ? points : [];
  const byName = new Map<string, Point[]>();
  for (const item of list) {
    const px = getPath(item, x);
    const py = numberOf(getPath(item, y));
    if (px === undefined || px === null || py === null) continue;
    const name = seriesBy ? asText(getPath(item, seriesBy)) : "";
    const bucket = byName.get(name) ?? [];
    bucket.push({ x: typeof px === "string" ? px : asText(px), y: py });
    byName.set(name, bucket);
  }
  const all = [...byName].map(([name, pts]) => ({ name, points: pts }));
  return { series: all.slice(0, MAX_SERIES), extra: Math.max(0, all.length - MAX_SERIES) };
}

/** The points of a sparkline column: the list at the column path, `y` of each element; the x of a point is its `t` when it has one. */
export function sparkPoints(value: unknown, y: string): { t: string; v: number }[] {
  if (!Array.isArray(value)) return [];
  const out: { t: string; v: number }[] = [];
  value.forEach((item, i) => {
    const v = numberOf(getPath(item, y));
    if (v !== null) out.push({ t: isObj(item) && typeof item.t === "string" ? item.t : String(i), v });
  });
  return out;
}

/** True when an x value reads as a time, so the axis can show it as one. */
export function isTime(x: string): boolean {
  return /^\d{4}-\d{2}-\d{2}/.test(x) && !Number.isNaN(Date.parse(x));
}

// ---------------------------------------------------------------- an action's inputs

export interface InputSpec { name: string; label: string; type: string; required?: boolean; options?: string[]; min?: number; max?: number; maxLength?: number }

/** The JSON body the form's text values make, or one error per field. The BFF checks again (it is the authority); this only saves a round trip. */
export function readInputs(specs: InputSpec[], values: Record<string, string | boolean | undefined>): { body: Obj; errors: Record<string, string> } {
  const body: Obj = {};
  const errors: Record<string, string> = {};
  for (const spec of specs) {
    const raw = values[spec.name];
    const empty = raw === undefined || raw === "" || (spec.type === "boolean" && raw === undefined);
    if (spec.type === "boolean") {
      if (raw === undefined) { if (spec.required) errors[spec.name] = "is required"; continue; }
      body[spec.name] = raw === true || raw === "true";
      continue;
    }
    if (empty) { if (spec.required) errors[spec.name] = "is required"; continue; }
    const text = String(raw);
    if (spec.type === "string") {
      if (text.length > (spec.maxLength ?? 500)) errors[spec.name] = `is longer than ${spec.maxLength ?? 500} characters`;
      else body[spec.name] = text;
    } else if (spec.type === "enum") {
      if (!(spec.options ?? []).includes(text)) errors[spec.name] = "is not one of the options";
      else body[spec.name] = text;
    } else {
      // GUI-10.3: Number("  ") is 0; a whitespace-only number is a blank field (required, or left out), never a zero
      if (text.trim() === "") { if (spec.required) errors[spec.name] = "is required"; continue; }
      const n = Number(text);
      if (!Number.isFinite(n)) errors[spec.name] = "must be a number";
      else if (spec.type === "integer" && !Number.isInteger(n)) errors[spec.name] = "must be a whole number";
      else if (spec.min !== undefined && n < spec.min) errors[spec.name] = `must be at least ${spec.min}`;
      else if (spec.max !== undefined && n > spec.max) errors[spec.name] = `must be at most ${spec.max}`;
      else body[spec.name] = n;
    }
  }
  return { body, errors };
}

/** The polling interval in ms for a source's `refreshSeconds`, or false for "when the page opens and on a manual refresh". Out-of-range values are clamped to the format's 5 to 3600. */
export function refreshInterval(seconds: unknown): number | false {
  const n = numberOf(seconds);
  return n === null ? false : Math.min(3600, Math.max(5, n)) * 1000;
}
