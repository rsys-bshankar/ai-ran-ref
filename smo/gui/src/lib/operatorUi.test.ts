import { describe, expect, it } from "vitest";

import { formatTime } from "./domain";
import {
  asText, fillQuery, fillRoute, fillTitle, formatNumber, formatValue, getPath, listAt, readInputs, refreshInterval, safeSegment, sparkPoints, toSeries, whenMatches,
} from "./operatorUi";

describe("getPath", () => {
  const answer = { a: { b: { c: 5 } }, list: [{ k: 1 }, { k: 2 }, {}], datasets: [{ dataset: "x" }, { dataset: "y" }], zero: 0, nothing: null };

  it("walks dotted names and returns undefined for anything missing", () => {
    expect(getPath(answer, "a.b.c")).toBe(5);
    expect(getPath(answer, "zero")).toBe(0);
    expect(getPath(answer, "nothing")).toBeNull();
    expect(getPath(answer, "a.x.c")).toBeUndefined();
    expect(getPath(answer, "a.b.c.d")).toBeUndefined();
    expect(getPath(null, "a")).toBeUndefined();
    expect(getPath("text", "a")).toBeUndefined();
    expect(getPath([1, 2], "0")).toBeUndefined();
  });

  it("walks every element of a list with []", () => {
    expect(getPath(answer, "datasets[].dataset")).toEqual(["x", "y"]);
    expect(getPath(answer, "list[].k")).toEqual([1, 2, undefined]);
    expect(getPath(answer, "list[]")).toEqual(answer.list);
    expect(getPath(answer, "a[].k")).toBeUndefined();
    expect(getPath(answer, "missing[].k")).toBeUndefined();
  });

  it("never follows a name the object does not have itself", () => {
    for (const name of ["constructor", "__proto__", "toString", "hasOwnProperty", "prototype"]) {
      expect(getPath({}, name)).toBeUndefined();
      expect(getPath({ a: {} }, `a.${name}`)).toBeUndefined();
    }
    expect(getPath(JSON.parse('{"constructor": 3}'), "constructor")).toBe(3);
  });

  it("listAt takes the whole answer for the empty path and only a list counts", () => {
    expect(listAt([1, 2], undefined)).toEqual([1, 2]);
    expect(listAt([1, 2], "")).toEqual([1, 2]);
    expect(listAt({ cells: [1] }, "cells")).toEqual([1]);
    expect(listAt({ cells: "no" }, "cells")).toBeUndefined();
    expect(listAt({}, "cells")).toBeUndefined();
  });
});

describe("routes", () => {
  it("accepts only one safe segment", () => {
    expect(safeSegment("C-1_x.y~z")).toBe("C-1_x.y~z");
    expect(safeSegment(42)).toBe("42");
    for (const bad of ["", ".", "..", "a/b", "a b", "a%2Fb", "a?b", "a#b", "évil", "a\n", null, undefined, {}, [], true]) expect(safeSegment(bad)).toBeNull();
  });

  it("fills {instanceId} and {row.<field>} from the open instance and the clicked row only", () => {
    const row = { cellId: "C1", other: "x" };
    expect(fillRoute("/instances/{instanceId}/cells/{row.cellId}/override", "iid-1", row)).toBe("/instances/iid-1/cells/C1/override");
    expect(fillRoute("/instances/{instanceId}", "iid-1")).toBe("/instances/iid-1");
    expect(fillRoute("/c/{row.missing}/x", "iid-1", row)).toBeNull();
    expect(fillRoute("/c/{row.cellId}/x", "iid-1")).toBeNull();
    expect(fillRoute("/c/{row.cellId}/x", "iid-1", { cellId: "../x" })).toBeNull();
    expect(fillRoute("/c/{row.cellId}/x", "iid-1", { cellId: "a/b" })).toBeNull();
    expect(fillRoute("/c/{row.constructor}/x", "iid-1", {})).toBeNull();
    expect(fillRoute("/c/{other}/x", "iid-1", row)).toBeNull();
    expect(fillRoute("/instances/{instanceId}", "../x")).toBeNull();
  });

  it("fills a whole-value {row.<field>} in a query and keeps fixed values", () => {
    expect(fillQuery({ cell_id: "{row.cellId}", limit: 20, all: true, mode: "x" }, { cellId: "C1" })).toEqual({ cell_id: "C1", limit: 20, all: true, mode: "x" });
    expect(fillQuery({ points: 48 })).toEqual({ points: 48 });
    expect(fillQuery(undefined)).toEqual({});
    expect(fillQuery({ cell_id: "{row.cellId}" }, { cellId: "a b" })).toBeNull();
    expect(fillQuery({ cell_id: "{row.cellId}" }, {})).toBeNull();
    expect(fillQuery({ a: { nested: 1 } as never })).toEqual({});
  });

  it("fills a title with plain text", () => {
    expect(fillTitle("Cell {row.cellId}", { cellId: "C1" })).toBe("Cell C1");
    expect(fillTitle("Cell {row.cellId}", { cellId: "<script>" })).toBe("Cell <script>");
    expect(fillTitle("Cell {row.cellId}", {})).toBe("Cell —");
    expect(fillTitle("Relation {row.source} → {row.target}", { source: "A", target: "B" })).toBe("Relation A → B");
    expect(fillTitle("No refs", {})).toBe("No refs");
  });
});

describe("when", () => {
  it("exists / equals / notEquals on a field of the row", () => {
    expect(whenMatches({ path: "overrideBy", exists: false }, {})).toBe(true);
    expect(whenMatches({ path: "overrideBy", exists: false }, { overrideBy: "alice" })).toBe(false);
    expect(whenMatches({ path: "overrideBy", exists: true }, { overrideBy: "alice" })).toBe(true);
    expect(whenMatches({ path: "overrideBy", exists: true }, { overrideBy: null })).toBe(false);
    expect(whenMatches({ path: "overrideBy", exists: true }, { overrideBy: "" })).toBe(false);
    expect(whenMatches({ path: "s", equals: "SLEEP" }, { s: "SLEEP" })).toBe(true);
    expect(whenMatches({ path: "s", equals: "SLEEP" }, { s: "SERVING" })).toBe(false);
    expect(whenMatches({ path: "s", notEquals: "SLEEP" }, { s: "SERVING" })).toBe(true);
    expect(whenMatches({ path: "a.b", equals: 3 }, { a: { b: 3 } })).toBe(true);
  });

  it("shows the button for a when it does not understand", () => {
    expect(whenMatches(undefined, {})).toBe(true);
    expect(whenMatches({ path: 5 }, {})).toBe(true);
    expect(whenMatches({ path: "x", weird: 1 }, {})).toBe(true);
  });
});

describe("formatValue", () => {
  it("shows a missing value as a dash", () => {
    for (const f of [undefined, "text", "number", "percent", "datetime", "badge", "id", "boolean", "list"]) {
      expect(formatValue(undefined, f)).toBe("—");
      expect(formatValue(null, f)).toBe("—");
      expect(formatValue("", f)).toBe("—");
    }
  });

  it("formats numbers, percents, booleans and lists", () => {
    expect(formatValue(3.14159, "number")).toBe("3.14");
    expect(formatValue(1234.5, "number")).toBe("1235");
    expect(formatValue(12, "number", "dB")).toBe("12 dB");
    expect(formatValue(37.256, "percent")).toBe("37.3 %");
    expect(formatValue(0, "number")).toBe("0");
    expect(formatValue(true, "boolean")).toBe("yes");
    expect(formatValue(false, "boolean")).toBe("no");
    expect(formatValue(["a", "b", 3], "list")).toBe("a, b, 3");
    expect(formatValue([], "list")).toBe("—");
  });

  it("shows a value that does not fit its format as text", () => {
    expect(formatValue("abc", "number")).toBe("abc");
    expect(formatValue({ a: 1 }, "percent")).toBe('{"a":1}');
    expect(formatValue("yes", "boolean")).toBe("yes");
    expect(formatValue("a", "list")).toBe("a");
    expect(formatValue("not a date", "datetime")).toBe("not a date");
    expect(formatValue(5, "unknown-format")).toBe("5");
    expect(formatValue("x", "badge")).toBe("x");
  });

  it("shortens very long structures and leaves markup as the characters it is", () => {
    expect(formatValue({ a: "x".repeat(500) }).length).toBeLessThanOrEqual(200);
    expect(asText("<img src=x onerror=alert(1)>")).toBe("<img src=x onerror=alert(1)>");
  });

  it("formats a datetime as the GUI formats other times", () => {
    expect(formatValue("2026-10-08T10:30:00Z", "datetime")).not.toBe("2026-10-08T10:30:00Z");
    expect(formatValue("2026-10-08T10:30:00Z", "datetime")).toBe(formatTime("2026-10-08T10:30:00Z"));
  });

  it("formatNumber keeps three significant digits below 100", () => {
    expect([formatNumber(0.123456), formatNumber(99.99), formatNumber(100.4), formatNumber(-250.6)]).toEqual(["0.123", "100", "100", "-251"]);
  });
});

describe("toSeries and sparkPoints", () => {
  const points = [
    { t: "2026-10-08T10:00:00Z", v: 1, kind: "A" }, { t: "2026-10-08T10:05:00Z", v: 2, kind: "A" }, { t: "2026-10-08T10:00:00Z", v: 5, kind: "B" },
    { t: "2026-10-08T10:10:00Z", v: "7", kind: "B" }, { t: "x", v: "no", kind: "B" }, { v: 3, kind: "B" },
  ];

  it("splits the points into one series per seriesBy value, leaving out points that are not numbers or have no x", () => {
    const { series, extra } = toSeries(points, "t", "v", "kind");
    expect(series.map((s) => [s.name, s.points.map((p) => p.y)])).toEqual([["A", [1, 2]], ["B", [5, 7]]]);
    expect(extra).toBe(0);
    expect(toSeries(points, "t", "v").series[0].points.length).toBe(4);
  });

  it("draws at most eight series and says how many it did not", () => {
    const many = Array.from({ length: 11 }, (_, i) => ({ x: "a", y: i, s: `s${i}` }));
    const { series, extra } = toSeries(many, "x", "y", "s");
    expect(series).toHaveLength(8);
    expect(extra).toBe(3);
  });

  it("is empty for anything that is not a list", () => {
    expect(toSeries(undefined, "t", "v").series).toEqual([]);
    expect(toSeries({ a: 1 }, "t", "v").series).toEqual([]);
  });

  it("sparkPoints takes y of each element and t when there is one", () => {
    expect(sparkPoints([{ t: "a", v: 1 }, { t: "b", v: "x" }, { v: 3 }, 7], "v")).toEqual([{ t: "a", v: 1 }, { t: "2", v: 3 }]);
    expect(sparkPoints(undefined, "v")).toEqual([]);
    expect(sparkPoints([{ a: { b: 2 } }], "a.b")).toEqual([{ t: "0", v: 2 }]);
  });
});

describe("readInputs", () => {
  const specs = [
    { name: "reason", label: "Reason", type: "string", required: true, maxLength: 5 },
    { name: "level", label: "Level", type: "integer", min: 1, max: 5 },
    { name: "gain", label: "Gain", type: "number", min: 0.5 },
    { name: "force", label: "Force", type: "boolean" },
    { name: "mode", label: "Mode", type: "enum", options: ["a", "b"] },
  ];

  it("makes typed values from the form's text", () => {
    expect(readInputs(specs, { reason: "ok", level: "3", gain: "0.5", force: true, mode: "b" })).toEqual({
      body: { reason: "ok", level: 3, gain: 0.5, force: true, mode: "b" }, errors: {} });
  });

  it("names the field that is wrong and leaves optional ones out", () => {
    const out = readInputs(specs, { reason: "toolong", level: "9", gain: "0.1", mode: "c" });
    expect(Object.keys(out.errors).sort()).toEqual(["gain", "level", "mode", "reason"]);
    expect(readInputs(specs, {}).errors).toEqual({ reason: "is required" });
    expect(readInputs(specs, { reason: "x", level: "1.5" }).errors).toEqual({ level: "must be a whole number" });
    expect(readInputs(specs, { reason: "x", gain: "abc" }).errors).toEqual({ gain: "must be a number" });
    expect(readInputs(specs, { reason: "x" }).body).toEqual({ reason: "x" });
  });

  it("a required boolean must be answered", () => {
    expect(readInputs([{ name: "f", label: "F", type: "boolean", required: true }], {}).errors).toEqual({ f: "is required" });
    expect(readInputs([{ name: "f", label: "F", type: "boolean", required: true }], { f: false }).body).toEqual({ f: false });
  });
});

describe("refreshInterval", () => {
  it("is off when absent and clamped to the format's range", () => {
    expect(refreshInterval(undefined)).toBe(false);
    expect(refreshInterval("x")).toBe(false);
    expect(refreshInterval(15)).toBe(15_000);
    expect(refreshInterval(1)).toBe(5_000);
    expect(refreshInterval(99999)).toBe(3_600_000);
  });
});
