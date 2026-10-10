/**
 * The SPA's only transport: same-origin calls to the BFF under /api (never to R1 Termination or a module directly).
 *
 * Session authentication rides on the BFF's httpOnly cookie, which this code cannot read; every unsafe method (POST, PUT, PATCH, DELETE)
 * also echoes the readable CSRF cookie back as the X-CSRF-Token header (double submit). `api` turns every non-2xx answer into an
 * `ApiError` with one readable line (`describeError`); `smo` is `api` under the BFF's /smo proxy to one SMO module.
 * Used by `api/hooks.ts`, `api/rapps.ts`, `auth/AuthContext.tsx` and the pages that call the BFF directly.
 */

/**
 * Query parameters of a call: a scalar, or an array of scalars that is sent as a repeated key; `buildQuery` drops null, undefined and empty values.
 */
export type Query = Record<string, string | number | boolean | null | undefined | Array<string | number>>;

/**
 * A failed BFF call: the HTTP status, the one-line `title` and optional `detail` that `describeError` read from the answer, and the parsed body.
 * `message` is "title: detail" (or the title alone), which the toasts and error boxes show as is.
 */
export class ApiError extends Error {
  constructor(
    public status: number,
    public title: string,
    public detail: string | undefined,
    public body: unknown,
  ) {
    super(detail ? `${title}: ${detail}` : title);
  }
}

export const CSRF_COOKIE = "smo_csrf";
const UNSAFE = new Set(["POST", "PUT", "PATCH", "DELETE"]);

/**
 * Returns the decoded value of cookie `name` from a `document.cookie`-style string, or undefined when it is absent.
 * Splits each pair on the first "=" only, so a value that itself contains "=" survives (base64 padding).
 */
export function readCookie(cookieString: string, name: string): string | undefined {
  for (const part of cookieString.split(";")) {
    const [k, ...rest] = part.trim().split("=");
    if (k === name) return decodeURIComponent(rest.join("="));
  }
  return undefined;
}

/**
 * Builds the query string ("?a=1&b=2", or "" when nothing is left) for a request.
 * Drops undefined, null and empty-string values so an unset filter is not sent, and repeats the key for an array (`ids=a&ids=b`).
 */
export function buildQuery(query?: Query): string {
  if (!query) return "";
  const params = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((item) => params.append(k, String(item)));
    else params.append(k, String(v));
  }
  const s = params.toString();
  return s ? `?${s}` : "";
}

/** Turns any backend error body into one readable line. Handles the BFF's
 * own RFC 7807-ish {title, detail}, the SMO modules' framework errors, and
 * FastAPI's {detail: "..."} / {detail: [{msg}]} validation shapes. */
export function describeError(status: number, body: unknown): { title: string; detail?: string } {
  if (body && typeof body === "object") {
    const b = body as Record<string, unknown>;
    const detail = b.detail;
    if (Array.isArray(detail)) {
      const msgs = detail.map((d) => (d && typeof d === "object" ? `${(d as { loc?: unknown[] }).loc?.slice(1).join(".") ?? ""} ${(d as { msg?: string }).msg ?? ""}`.trim() : String(d)));
      return { title: String(b.title ?? `HTTP ${status}`), detail: msgs.join("; ") };
    }
    // an error raised from a dependency of the BFF (a revoked session, MFA_ENROLMENT_REQUIRED) is wrapped as {detail: {title, status, detail}}
    if (detail && typeof detail === "object" && typeof (detail as { title?: unknown }).title === "string") {
      const inner = detail as { title: string; detail?: unknown };
      return { title: inner.title, detail: typeof inner.detail === "string" ? inner.detail : undefined };
    }
    if (typeof b.title === "string") return { title: b.title, detail: typeof detail === "string" ? detail : undefined };
    if (typeof detail === "string") return { title: `HTTP ${status}`, detail };
    if (typeof b.error === "string") return { title: b.error, detail: typeof b.error_description === "string" ? b.error_description : undefined };
  }
  if (typeof body === "string" && body) return { title: `HTTP ${status}`, detail: body.slice(0, 200) };
  return { title: `HTTP ${status}` };
}

/**
 * What `api` and `smo` accept: the HTTP method (default GET), the query, a JSON body (`json`, which also sets the content type) or a raw `body`, and an abort signal.
 */
export interface RequestOptions {
  method?: string;
  query?: Query;
  json?: unknown;
  body?: BodyInit;
  signal?: AbortSignal;
  /** Extra request headers (the declared-action id of a rApp page). The content type, the accept type and the CSRF token are set here. */
  headers?: Record<string, string>;
}

/**
 * Calls the BFF at `/api<path>` and returns the parsed JSON (or the raw text for a non-JSON answer, or undefined for an empty one).
 * Throws `ApiError` for any non-2xx answer. An unsafe method carries the CSRF header when the cookie is present. A 401 on any path other
 * than the two sign-in routes also fires the window event `smo:unauthorized`, which `AuthProvider` answers by dropping the session so the app
 * returns to the login page; a wrong password on /login must not trigger that, hence the exclusion.
 */
export async function api<T = unknown>(path: string, opts: RequestOptions = {}): Promise<T> {
  const method = (opts.method ?? "GET").toUpperCase();
  const headers: Record<string, string> = { Accept: "application/json", ...opts.headers };
  let body = opts.body;
  if (opts.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(opts.json);
  }
  if (UNSAFE.has(method)) {
    const csrf = readCookie(document.cookie, CSRF_COOKIE);
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }
  const resp = await fetch(`/api${path}${buildQuery(opts.query)}`, {
    method, headers, body, credentials: "same-origin", signal: opts.signal,
  });
  const text = await resp.text();
  let parsed: unknown = text;
  if (text && (resp.headers.get("content-type") ?? "").includes("json")) {
    try { parsed = JSON.parse(text); } catch { /* keep text */ }
  }
  if (!resp.ok) {
    // The sign-in routes answer 401 for a wrong password or code; that is an ordinary form error, not an expired session.
    if (resp.status === 401 && path !== "/login" && path !== "/login/totp") window.dispatchEvent(new Event("smo:unauthorized"));
    const { title, detail } = describeError(resp.status, parsed);
    throw new ApiError(resp.status, title, detail, parsed);
  }
  return (text ? parsed : undefined) as T;
}

/** A call into one SMO module through the BFF proxy: smo("/rapp-mgmt/instances"). */
export function smo<T = unknown>(path: string, opts: RequestOptions = {}): Promise<T> {
  return api<T>(`/smo${path}`, opts);
}
