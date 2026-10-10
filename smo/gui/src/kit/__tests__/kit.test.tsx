// @vitest-environment jsdom
/** Component tests of the shared kit: the pager's wording and paging (SCALE.md P1), the server table's query (limit/offset, filter reset),
 * the diff builder, KPI tiles and the explicit states. Uses the fake BFF of src/testing/bff.tsx. Run: `npx vitest run src/kit`. */
import { afterEach, describe, expect, it } from "vitest";

import { fakeBff, mountWith } from "../../testing/bff";
import { byText, cleanup, click, mount, settle } from "../../testing/dom";
import { diffLines, Diff } from "../Diff";
import { formatCount, Kpi } from "../Kpi";
import { Pager } from "../Pager";
import { ServerTable } from "../ServerTable";
import { QueryState } from "../states";

afterEach(cleanup);

describe("Pager", () => {
  // The footer always says how much of the list is shown, so a cut list is never silent.
  it("says 'Showing 1–50 of 12,480' when the backend counted and '· more' when it did not", async () => {
    const { container } = await mount(<Pager offset={0} limit={50} shown={50} total={12480} onOffset={() => {}} />);
    expect(container.textContent).toContain("Showing 1–50 of 12,480");
    cleanup();
    const second = await mount(<Pager offset={50} limit={50} shown={50} hasMore onOffset={() => {}} />);
    expect(second.container.textContent).toContain("Showing 51–100 · more");
  });

  // Next is disabled on the last page, Previous on the first.
  it("disables the buttons at the ends", async () => {
    const { container } = await mount(<Pager offset={0} limit={50} shown={10} total={10} onOffset={() => {}} />);
    expect(byText<HTMLButtonElement>(container, "button", "← Previous")!.disabled).toBe(true);
    expect(byText<HTMLButtonElement>(container, "button", "Next →")!.disabled).toBe(true);
  });
});

describe("ServerTable", () => {
  // Paging and filtering are the backend's: the table sends limit/offset and the filter, and Next asks for the next offset.
  it("asks the module for one page with its filters and pages forward on Next", async () => {
    const calls = fakeBff({ "GET /smo/ran-nf-oam/alarms": (c: { query: URLSearchParams }) => ({ items: [{ id: `a${c.query.get("offset")}` }], total: 120, limit: 50, offset: Number(c.query.get("offset")) }) });
    const { container } = await mountWith(<ServerTable<{ id: string }> path="/ran-nf-oam/alarms" query={{ severity: "critical" }} rowKey={(r) => r.id}
      columns={[{ header: "Id", render: (r) => r.id }]} />);
    await settle();
    expect(calls[0].query.get("severity")).toBe("critical");
    expect(calls[0].query.get("limit")).toBe("50");
    expect(calls[0].query.get("offset")).toBe("0");
    expect(container.textContent).toContain("of 120");
    await click(byText(container, "button", "Next →")!);
    await settle();
    expect(calls.at(-1)!.query.get("offset")).toBe("50");
  });

  // A huge forward-only list asks for no count (no COUNT(*) on the backend).
  it("sends total=false when asked not to count", async () => {
    const calls = fakeBff({ "GET /smo/ran-nf-oam/decision-records": { items: [], limit: 50, offset: 0, hasMore: false } });
    await mountWith(<ServerTable<{ id: string }> path="/ran-nf-oam/decision-records" withTotal={false} rowKey={(r) => r.id} columns={[]} />);
    await settle();
    expect(calls[0].query.get("total")).toBe("false");
  });
});

describe("Diff and KPI tiles", () => {
  // Only changed keys appear, as a removed and an added line.
  it("builds -/+ lines for changed keys only", async () => {
    expect(diffLines({ cio: 0, tilt: 2 }, { cio: 2, tilt: 2 })).toEqual([{ kind: "m", text: "- cio: 0" }, { kind: "p", text: "+ cio: 2" }]);
    const { container } = await mount(<Diff lines={diffLines({ a: 1 }, { a: 2 })} />);
    expect(container.querySelectorAll(".diff .m, .diff .p")).toHaveLength(2);
  });

  // A value the backend does not serve shows "—", never a made-up number.
  it("shows a dash for an unknown value and formats large counts", async () => {
    const { container } = await mount(<Kpi label="Network health" value={null} />);
    expect(container.querySelector(".kpi-v")!.textContent).toBe("—");
    expect(formatCount(12480)).toBe("12.5k");
    expect(formatCount(1500, 999)).toBe("999+");
  });

  // Every box has a loading, an empty and an error-with-retry state.
  it("renders the explicit states", async () => {
    let retried = false;
    const { container } = await mount(<>
      <QueryState q={{ data: undefined }}><b>x</b></QueryState>
      <QueryState q={{ data: [] }} empty="No alarms."><b>x</b></QueryState>
      <QueryState q={{ data: undefined, error: new Error("boom"), refetch: () => { retried = true; } }}><b>x</b></QueryState>
    </>);
    expect(container.querySelector("[aria-busy=true]")).not.toBeNull();
    expect(container.textContent).toContain("No alarms.");
    await click(byText(container, "button", "Retry")!);
    expect(retried).toBe(true);
  });
});
