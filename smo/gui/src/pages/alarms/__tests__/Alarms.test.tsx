// @vitest-environment jsdom
/** Tests of the Alarms page (pages/alarms): severity tiles from the summary that toggle the table's `severity` parameter, `?me=` as the starting
 * managed element filter, the detail panel with Ack and the lifecycle, the "same element within 60 s" root-cause heuristic, the page-local
 * ack filter, and the O-Cloud / FM subscription tabs. Run: `npx vitest run src/pages/alarms`. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../../../auth/AuthContext";
import rules from "../../../auth/permissions.fixture.json";
import { fakeBff, mountWith, type Call } from "../../../testing/bff";
import { byText, cleanup, click, settle } from "../../../testing/dom";
import { Alarms } from "..";
import { sameElementWithin } from "../data/queries";

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
beforeEach(() => { document.body.innerHTML = ""; window.location.hash = ""; });

const T0 = "2026-10-09T10:00:00Z";
const plus = (s: number) => new Date(Date.parse(T0) + s * 1000).toISOString();

const alarm = (extra: Record<string, unknown> = {}) => ({
  alarmId: "a-1", sourceAlarmId: "odu-17", managedElementRef: "ME-1", managedFunctionRef: "NRCellDU=101", severity: "critical", ackState: "UNACKNOWLEDGED",
  raisedAt: T0, correlationGroup: null, probableCause: "LOSS_OF_SIGNAL", specificProblem: "CPRI link down", rootCauseIndicator: false, correlatedNotifications: [],
  proposedRepairActions: null, alarmType: "COMMUNICATIONS_ALARM", ackUserId: null, changedAt: null, clearedAt: null, clearUserId: null, ...extra,
});

/** The fake BFF of these tests: a signed-in user of `role`, the alarm summary, RAN alarms (the hint's limit-20 read gets three of ME-1), O-Cloud and FM lists. */
function bff(role: "viewer" | "operator" | "admin" = "operator", overrides: Record<string, unknown> = {}) {
  return fakeBff({
    "GET /me": { username: "ana", role, csrfToken: "c", local: true, totpEnrolled: true, mfaEnrolmentRequired: false },
    "GET /permissions": { role, rules },
    "GET /summary/alarms": { page: "alarms", computedAt: "", partial: [], counts: {
      "alarms.critical": 37, "alarms.major": 180, "alarms.minor": 488, "alarms.warning": 579, "alarms.cleared": 16, "alarms.total": 1300, "ocloudAlarms.total": 4 } },
    "GET /smo/ran-nf-oam/alarms": (c: Call) => (c.query.get("limit") === "20"
      ? { items: [alarm(), alarm({ alarmId: "a-2", severity: "major", raisedAt: plus(40), probableCause: "LINK_DOWN" }), alarm({ alarmId: "a-3", raisedAt: plus(400) })], limit: 20, offset: 0, hasMore: false }
      : { items: [alarm(), alarm({ alarmId: "a-9", managedElementRef: "ME-2", severity: "minor", ackState: "ACKNOWLEDGED", ackUserId: "smo-gui:bob" })], limit: 50, offset: 0, total: 2 }),
    "GET /smo/focom/alarms": { items: [{ alarmId: "o-1", resourceRef: "node-7", severity: "major" }], limit: 50, offset: 0, total: 1 },
    "GET /smo/ran-nf-oam/fm-subscriptions": { items: [{ subscriptionId: "s-1", managedElementRef: "ME-1", deliveryMethod: "push", southboundEngine: "netconf" }], limit: 50, offset: 0, total: 1 },
    "GET /smo/ran-nf-oam/o1-adaptor-endpoints": { items: [], limit: 100, offset: 0 },
    "PATCH /smo/ran-nf-oam/alarms/a-1/ack": { body: alarm({ ackState: "ACKNOWLEDGED" }) },
    ...overrides,
  });
}

const open = (at = "/alarms") => mountWith(<AuthProvider><Alarms /></AuthProvider>, { at });
const tableCalls = (calls: Call[]) => calls.filter((c) => c.path === "/smo/ran-nf-oam/alarms" && c.query.get("limit") !== "20");

describe("the alarm page", () => {
  // Pins down: the tiles show the server's counts, and a tile click toggles the table's severity query parameter on and off.
  it("shows true counts per severity, and a severity tile toggles the table's severity filter", async () => {
    const calls = bff();
    const { container } = await open();
    await settle();
    const tiles = container.querySelector("[data-section='alarms.tiles']")!;
    expect(tiles.textContent).toContain("37");
    expect(tiles.textContent).toContain("1,284");                                     // open = total − cleared
    expect(tableCalls(calls).at(-1)!.query.get("severity")).toBeNull();
    const critical = Array.from(tiles.querySelectorAll("button.kpi")).find((b) => b.textContent?.includes("critical")) as HTMLElement;
    await click(critical);
    await settle();
    expect(critical.getAttribute("aria-pressed")).toBe("true");
    expect(tableCalls(calls).at(-1)!.query.get("severity")).toBe("critical");
    expect(tableCalls(calls).at(-1)!.query.get("offset")).toBe("0");
    await click(critical);
    await settle();
    expect(tableCalls(calls).at(-1)!.query.get("severity")).toBeNull();
    expect(tiles.textContent).toContain("Time to acknowledge");
    expect(tiles.textContent).toContain("not measured yet");                           // a data gap shows "—", never a made-up number
  });

  // Pins down: the global search link /alarms?me=X starts the table filtered on that managed element.
  it("starts filtered on the managed element named in ?me=", async () => {
    const calls = bff();
    const { container } = await open("/alarms?me=ME-7");
    await settle();
    expect(tableCalls(calls)[0].query.get("managed_element_ref")).toBe("ME-7");
    expect((container.querySelector("input[aria-label='Managed element']") as HTMLInputElement).value).toBe("ME-7");
  });

  // Pins down: a row opens the detail panel with the TS 28.532 fields, the lifecycle and Ack, and the hint lists alarms of the same element within 60 s only.
  it("shows the selected alarm with its lifecycle, Ack, and the same-element-within-60-s hint", async () => {
    const calls = bff();
    const { container } = await open();
    await settle();
    await click(container.querySelector("tbody tr") as HTMLElement);
    await settle();
    const detail = container.querySelector("[data-section='alarms.detail']") as HTMLElement;
    expect(detail.textContent).toContain("LOSS_OF_SIGNAL");
    expect(detail.querySelectorAll("ol[aria-label='Alarm lifecycle'] > li")).toHaveLength(3);
    const hint = container.querySelector("[data-section='alarms.rootcause']")!;
    expect(hint.textContent).toContain("1 other alarm on ME-1 within 60 s");
    expect(hint.textContent).toContain("LINK_DOWN");
    expect(hint.textContent).toContain("Heuristic");
    const near = calls.find((c) => c.query.get("limit") === "20")!;
    expect(near.query.get("managed_element_ref")).toBe("ME-1");
    await click(byText(detail, "button", "Ack")!);
    await settle();
    const patch = calls.find((c) => c.method === "PATCH")!;
    expect(patch.path).toBe("/smo/ran-nf-oam/alarms/a-1/ack");
    expect(patch.query.get("new_state")).toBe("ACKNOWLEDGED");
  });

  // Pins down: a viewer sees alarms but no Ack/Clear button.
  it("shows a viewer no Ack or Clear", async () => {
    bff("viewer");
    const { container } = await open();
    await settle();
    expect(container.querySelectorAll("tbody tr")).toHaveLength(2);
    expect(byText(container, "button", "Ack")).toBeNull();
    expect(byText(container, "button", "Clear")).toBeNull();
  });

  // Pins down: the ack-state filter (no route parameter) narrows the current page and says how many rows it hid.
  it("narrows the page by ack state and says so", async () => {
    bff();
    const { container } = await open();
    await settle();
    const select = container.querySelector("select[aria-label='Ack state']") as HTMLSelectElement;
    select.value = "ACKNOWLEDGED";
    select.dispatchEvent(new Event("change", { bubbles: true }));
    await settle();
    expect(container.querySelectorAll("tbody tr")).toHaveLength(1);
    expect(container.querySelector("[data-hidden-rows]")?.getAttribute("data-hidden-rows")).toBe("1");
  });

  // Pins down: the O-Cloud and FM subscription tabs switch, keep the hash, and load only their own list.
  it("switches to the O-Cloud and FM subscription tabs", async () => {
    const calls = bff();
    const { container } = await open();
    await settle();
    await click(byText(container, "[role=tab]", /O-Cloud/)!);
    await settle();
    expect(window.location.hash).toBe("#ocloud");
    expect(container.querySelector("tbody")?.textContent).toContain("node-7");
    await click(byText(container, "[role=tab]", /FM subscriptions/)!);
    await settle();
    expect(container.querySelector("tbody")?.textContent).toContain("netconf");
    expect(calls.some((c) => c.path === "/smo/ran-nf-oam/fm-subscriptions")).toBe(true);
  });

  // Pins down: the heuristic ignores other elements, the alarm itself, and alarms outside the window.
  it("matches only the same element within the window", () => {
    const a = alarm() as never;
    const out = sameElementWithin(a, [alarm(), alarm({ alarmId: "x", raisedAt: plus(-59) }), alarm({ alarmId: "y", raisedAt: plus(61) }), alarm({ alarmId: "z", managedElementRef: "ME-2", raisedAt: plus(1) })] as never[]);
    expect(out.map((m) => [m.alarm.alarmId, m.deltaS])).toEqual([["x", -59]]);
  });
});
