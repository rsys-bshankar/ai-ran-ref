// @vitest-environment jsdom
/** Tests of the Exports page (pages/exports, GUI-9.5b) and the export rules (`data/exports.ts`): the job list with state, rows, size and the
 * download of a finished file; delete; the 2 s refresh while a job runs; an admin's per-user filter; and the requests the Decisions and audit
 * filters make. Uses the fake BFF of `src/testing/bff.tsx`. Run: `npx vitest run src/pages/exports` from smo/gui. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../../../auth/AuthContext";
import rules from "../../../auth/permissions.fixture.json";
import { ALL_SINCE, auditExport, decisionsExport, EXPORT_POLL, exportPoll, formatBytes } from "../../../data/exports";
import { fakeBff, mountWith, type Call } from "../../../testing/bff";
import { byText, cleanup, click, settle, type } from "../../../testing/dom";
import { Exports } from "../index";
import { filtersOf, spanOf } from "../sections/ExportList";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
beforeEach(() => { document.body.innerHTML = ""; });

/** A job as `GET /api/exports` shows it; `extra` overrides fields. */
const job = (extra: Record<string, unknown> = {}) => ({
  id: "e1", kind: "decisions", username: "ops", params: { since: "2026-10-01T00:00:00Z", disposition: "APPROVED", region: "north" }, state: "DONE",
  rows: 1234567, bytes: 5 * 1024 * 1024, error: null, createdAt: "2026-10-09T10:00:00Z", finishedAt: "2026-10-09T10:02:00Z",
  expiresAt: "2026-10-10T10:02:00Z", fileName: "smo-decisions-20261009T100000Z-e1.csv", fileUrl: "/api/exports/e1/file", ...extra,
});

/** A BFF for `role` holding `jobs`. */
function bff(role: "operator" | "admin", jobs: unknown[]) {
  return fakeBff({
    "GET /me": { username: "ops", role, csrfToken: "c", local: true, totpEnrolled: true },
    "GET /permissions": { role, rules },
    "GET /exports": { items: jobs },
    "DELETE /exports/e1": { status: 204 },
  });
}

describe("the export list", () => {
  // a finished job shows its rows, size and a download of its file; its filters and span are spelled out
  it("shows state, rows, size and the download of a finished job", async () => {
    bff("operator", [job(), job({ id: "e2", kind: "audit", state: "FAILED", error: "interrupted", fileUrl: null, rows: 10, bytes: 900, params: { since: ALL_SINCE } })]);
    const { container } = await mountWith(<AuthProvider><Exports /></AuthProvider>, { at: "/exports" });
    await settle();
    const rows = Array.from(container.querySelectorAll("tbody tr"));
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain("1,234,567");
    expect(rows[0].textContent).toContain("5.0 MB");
    expect(rows[0].textContent).toContain("disposition=APPROVED, region=north");
    const dl = rows[0].querySelector("a")!;
    expect(dl.getAttribute("href")).toBe("/api/exports/e1/file");
    expect(dl.getAttribute("download")).toBe("smo-decisions-20261009T100000Z-e1.csv");
    expect(rows[1].textContent).toContain("interrupted");
    expect(rows[1].textContent).toContain("first record");
    expect(rows[1].querySelector("a")).toBeNull();
  });

  // delete asks first, then sends DELETE /api/exports/{id}
  it("deletes a job after a confirmation", async () => {
    const calls = bff("operator", [job()]);
    vi.stubGlobal("confirm", () => true);
    const { container } = await mountWith(<AuthProvider><Exports /></AuthProvider>, { at: "/exports" });
    await settle();
    await click(byText(container, "button", "Delete")!);
    await settle();
    expect(calls.some((c) => c.method === "DELETE" && c.path === "/exports/e1")).toBe(true);
  });

  // a running job makes the list refresh every 2 s and says so; with none running it refreshes every 30 s
  it("follows running jobs", async () => {
    bff("operator", [job({ state: "RUNNING", fileUrl: null, finishedAt: null, rows: 5000 })]);
    const { container } = await mountWith(<AuthProvider><Exports /></AuthProvider>, { at: "/exports" });
    await settle();
    expect(container.textContent).toContain("1 running · refreshing every 2 s");
    expect(exportPoll([{ state: "QUEUED" }])).toBe(EXPORT_POLL.running);
    expect(exportPoll([{ state: "DONE" }, { state: "EXPIRED" }])).toBe(EXPORT_POLL.idle);
  });

  // an admin sees every user's jobs and can narrow them to one user (`?username=`)
  it("lets an admin narrow the list to one user", async () => {
    const calls = bff("admin", [job()]);
    const { container } = await mountWith(<AuthProvider><Exports /></AuthProvider>, { at: "/exports" });
    await settle();
    expect(container.querySelector("tbody")!.textContent).toContain("ops");
    await type(container.querySelector("input[aria-label='Filter by user']") as HTMLInputElement, "ana");
    await settle();
    expect(calls.filter((c: Call) => c.path === "/exports").at(-1)!.query.get("username")).toBe("ana");
  });
});

describe("export requests", () => {
  // the Decisions filters and the scope become the job's body; blank filters are left out; no start means the first record
  it("builds the decisions and audit requests", () => {
    expect(decisionsExport({ since: "2026-10-01T00:00:00Z", invoker_id: "inv", disposition: "", model_version: "m1" }, { region: "north", cluster: "c1" }))
      .toEqual({ kind: "decisions", since: "2026-10-01T00:00:00Z", invokerId: "inv", region: "north", siteCluster: "c1" });
    expect(decisionsExport({}, { region: null, cluster: null })).toEqual({ kind: "decisions", since: ALL_SINCE });
    expect(auditExport({ username: "", action: "LOGIN", since: null, until: "2026-10-02T00:00:00Z" }))
      .toEqual({ kind: "audit", since: ALL_SINCE, until: "2026-10-02T00:00:00Z", action: "LOGIN" });
  });

  // sizes and the summaries of a job's parameters
  it("formats sizes, filters and spans", () => {
    expect([formatBytes(null), formatBytes(512), formatBytes(2048), formatBytes(3 * 1024 ** 3)]).toEqual(["—", "512 B", "2.0 KB", "3.0 GB"]);
    expect(filtersOf({ params: { kind: "audit", since: "x", username: "ana" } })).toBe("username=ana");
    expect(spanOf({ params: { since: ALL_SINCE } })).toBe("first record → request time");
  });
});
