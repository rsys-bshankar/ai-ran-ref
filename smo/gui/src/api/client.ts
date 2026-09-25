// The SPA's only transport: same-origin calls to the BFF under /api.
// Session auth rides on the BFF's httpOnly cookie; every unsafe method also
// echoes the readable CSRF cookie back as X-CSRF-Token (double submit).

export type Query = Record<string, string | number | boolean | null | undefined | Array<string | number>>;

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

export function readCookie(cookieString: string, name: string): string | undefined {
  for (const part of cookieString.split(";")) {
    const [k, ...rest] = part.trim().split("=");
    if (k === name) return decodeURIComponent(rest.join("="));
  }
  return undefined;
}

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
    if (typeof b.title === "string") return { title: b.title, detail: typeof detail === "string" ? detail : undefined };
    if (typeof detail === "string") return { title: `HTTP ${status}`, detail };
    if (typeof b.error === "string") return { title: b.error, detail: typeof b.error_description === "string" ? b.error_description : undefined };
  }
  if (typeof body === "string" && body) return { title: `HTTP ${status}`, detail: body.slice(0, 200) };
  return { title: `HTTP ${status}` };
}

export interface RequestOptions {
  method?: string;
  query?: Query;
  json?: unknown;
  body?: BodyInit;
  signal?: AbortSignal;
}

export async function api<T = unknown>(path: string, opts: RequestOptions = {}): Promise<T> {
  const method = (opts.method ?? "GET").toUpperCase();
  const headers: Record<string, string> = { Accept: "application/json" };
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
    if (resp.status === 401 && path !== "/login") window.dispatchEvent(new Event("smo:unauthorized"));
    const { title, detail } = describeError(resp.status, parsed);
    throw new ApiError(resp.status, title, detail, parsed);
  }
  return (text ? parsed : undefined) as T;
}

/** A call into one SMO module through the BFF proxy: smo("/rapp-mgmt/instances"). */
export function smo<T = unknown>(path: string, opts: RequestOptions = {}): Promise<T> {
  return api<T>(`/smo${path}`, opts);
}
