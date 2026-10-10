/** Unit tests of the data layer: targeted invalidation (data/keys.ts), summary helpers (data/summary.ts) and the preferences guard
 * (data/preferences.ts). Pure functions, no DOM. Run: `npx vitest run src/data`. */
import { describe, expect, it } from "vitest";

import { invalidationTargets, isAffected } from "../keys";
import { applyToDocument, DEFAULT_PREFERENCES, sanitize } from "../preferences";
import { byState, count, openAlarms, sum, type Summary } from "../summary";

const S: Summary = { page: "x", computedAt: "", partial: [], counts: { "alarms.total": 5000, "alarms.cleared": 800, "alarms.critical": 1234, "alarms.major": null,
  "instances.RUNNING": 400, "instances.FAULTED": 0, "instances.total": 412 } };

describe("targeted invalidation", () => {
  // An ack on an alarm refreshes RAN NF OAM reads and the counts, never AI/ML or the session.
  it("refreshes only the module a change touches, its known dependents and the summary", () => {
    const mods = invalidationTargets("/ran-nf-oam/alarms/1/ack");
    expect(isAffected(["smo", "/ran-nf-oam/alarms", {}], mods)).toBe(true);
    expect(isAffected(["smo", "/aimgf/models", {}], mods)).toBe(false);
    expect(isAffected(["bff", "summary", "alarms"], mods)).toBe(true);
    expect(isAffected(["bff", "me"], mods)).toBe(false);
    expect(isAffected(["bff", "preferences"], mods)).toBe(false);
  });

  // A rApp instantiation also changes NFO and Onboarding, which the pre-redesign GUI covered by refetching everything.
  it("includes the cross-module effects of a rApp lifecycle call", () => {
    expect(invalidationTargets("/rapp-mgmt/instances")).toEqual(expect.arrayContaining(["rapp-mgmt", "nfo", "onboarding"]));
  });
});

describe("summary helpers", () => {
  // A count is a number or null; null means "its module did not answer", never zero.
  it("reads counts, keeps unknown as null and refuses a partial sum", () => {
    expect(count(S, "alarms.critical")).toBe(1234);
    expect(count(S, "alarms.major")).toBeNull();
    expect(sum(S, ["alarms.critical", "alarms.major"])).toBeNull();
    expect(sum(S, ["alarms.critical", "instances.RUNNING"])).toBe(1634);
  });

  // The backend has no "open" filter, so open alarms are every alarm minus the cleared ones.
  it("derives open alarms and per-state maps without zero entries or the total", () => {
    expect(openAlarms(S)).toBe(4200);
    expect(byState(S, "instances")).toEqual({ RUNNING: 400 });
  });
});

describe("preferences guard", () => {
  // theme, accent and size end up as attributes on <html>, so anything outside their sets is replaced by the default.
  it("drops values outside each field's set", () => {
    const p = sanitize({ theme: "neon", accent: "radisys", size: "xxl", rowsPerPage: 100, startPage: "javascript:alert(1)", reduceMotion: "yes" });
    expect(p).toEqual({ ...DEFAULT_PREFERENCES, accent: "radisys", rowsPerPage: 100 });
  });

  // The applied attributes are what styles.css keys the theme, accent and text size on.
  it("puts theme, accent, size and motion on the root element", () => {
    const el = { dataset: {} as Record<string, string> } as unknown as HTMLElement;
    applyToDocument({ ...DEFAULT_PREFERENCES, theme: "light", accent: "teal", size: "xl", reduceMotion: true }, el);
    expect(el.dataset).toEqual({ theme: "light", accent: "teal", size: "xl", motion: "reduce" });
  });
});
