// @vitest-environment jsdom
/** Tests of the pushed summary counts (data/events.ts, shell/LiveEvents.tsx), the top bar's live chip and the ⌘K box's server typeahead.
 * jsdom has no EventSource: the provider is given a fake source whose events the test fires by hand. Run: `npx vitest run src/shell`. */
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../../auth/AuthContext";
import { applySummaryEvent, criticalRose, listPathsFor, nextBackoff, summaryPageOf, topicsFor, useLive, type EventSourceLike } from "../../data/events";
import { KEYS } from "../../data/keys";
import { ScopeContext } from "../../data/scope";
import { fakeBff, mountWith } from "../../testing/bff";
import { cleanup, mount, settle, type } from "../../testing/dom";
import { GlobalSearch, resultsFor } from "../GlobalSearch";
import { LiveEvents } from "../LiveEvents";
import { TopBar } from "../TopBar";

/** The signed-in operator the shell components need. */
const ME = { "GET /me": { username: "ops", role: "operator", local: true, totpEnrolled: true }, "GET /permissions": { role: "operator", rules: [] } };

afterEach(() => { cleanup(); vi.useRealTimers(); });

/** A hand-driven stand-in for EventSource. */
class FakeSource implements EventSourceLike {
  readyState = 0;
  onopen: ((ev: Event) => unknown) | null = null;
  onerror: ((ev: Event) => unknown) | null = null;
  listeners: Record<string, ((ev: MessageEvent) => void)[]> = {};
  closed = false;
  constructor(public url: string) {}
  addEventListener(type: string, l: (ev: MessageEvent) => void) { (this.listeners[type] ??= []).push(l); }
  close() { this.closed = true; this.readyState = 2; }
  open() { this.readyState = 1; this.onopen?.(new Event("open")); }
  emit(type: string, data: unknown) { for (const l of this.listeners[type] ?? []) l(new MessageEvent(type, { data: JSON.stringify(data) })); }
  fail() { this.readyState = 2; this.onerror?.(new Event("error")); }
}

describe("event rules", () => {
  // The visible route picks the summary topic; the sidebar's "nav" is always there.
  it("maps routes to summary topics", () => {
    expect(summaryPageOf("/")).toBe("dashboard");
    expect(summaryPageOf("/alarms")).toBe("alarms");
    expect(summaryPageOf("/rapps/abc")).toBe("rapps");
    expect(summaryPageOf("/policy")).toBe("intents");
    expect(summaryPageOf("/preferences")).toBeNull();
    expect(topicsFor("alarms")).toEqual(["summary:nav", "summary:alarms"]);
    expect(topicsFor(null)).toEqual(["summary:nav"]);
  });

  // A changed count names the lists to refetch; the back-off doubles to its cap; only a real rise of critical alarms beeps.
  it("derives invalidations, back-off and the critical rise", () => {
    expect(listPathsFor(["alarms.critical", "alarms.total"])).toContain("/ran-nf-oam/alarms");
    expect(listPathsFor(["approvals.PENDING"])).toEqual(["/ran-nf-oam/rapp-approvals"]);
    expect(nextBackoff(0)).toBe(1000);
    expect(nextBackoff(1000)).toBe(2000);
    expect(nextBackoff(60_000)).toBe(60_000);
    expect(criticalRose(null, 3)).toBe(false);
    expect(criticalRose(2, 3)).toBe(true);
    expect(criticalRose(3, 3)).toBe(false);
  });

  // An event's counts land in the summary cache; the first event of a page refetches nothing, a later one refetches the alarm list only.
  it("writes counts into the cache and invalidates the affected lists", async () => {
    const qc = new QueryClient();
    qc.setQueryData(["smo", "/ran-nf-oam/alarms", "page", {}], { items: [] });
    qc.setQueryData(["smo", "/rapp-mgmt/instances", "page", {}], { items: [] });
    expect(applySummaryEvent(qc, { page: "alarms", counts: { "alarms.critical": 2 }, changed: ["alarms.critical"] }, { first: true })).toEqual([]);
    expect(qc.getQueryData<{ counts: Record<string, number> }>(KEYS.summary("alarms"))?.counts["alarms.critical"]).toBe(2);
    applySummaryEvent(qc, { page: "alarms", counts: { "alarms.critical": 3 }, changed: ["alarms.critical"] }, { first: false });
    expect(qc.getQueryState(["smo", "/ran-nf-oam/alarms", "page", {}])?.isInvalidated).toBe(true);
    expect(qc.getQueryState(["smo", "/rapp-mgmt/instances", "page", {}])?.isInvalidated).toBe(false);
  });

  // The Dashboard's panels (GUI-9.11) are replaced by an event that carries them and kept by one that carries none.
  it("replaces the panels an event carries and keeps them otherwise", () => {
    const qc = new QueryClient();
    const worst = () => qc.getQueryData<{ panels?: Record<string, unknown> }>(KEYS.summary("dashboard"))?.panels?.worst;
    applySummaryEvent(qc, { page: "dashboard", counts: {}, panels: { worst: [{ managedElementRef: "du-1" }] } }, { first: true });
    expect(worst()).toEqual([{ managedElementRef: "du-1" }]);
    applySummaryEvent(qc, { page: "dashboard", counts: { "alarms.critical": 1 }, changed: ["alarms.critical"] }, { first: false });
    expect(worst()).toEqual([{ managedElementRef: "du-1" }]);
    applySummaryEvent(qc, { page: "dashboard", counts: {}, panels: { worst: [] }, changed: ["panel.worst"] }, { first: false });
    expect(worst()).toEqual([]);
  });
});

describe("LiveEvents", () => {
  /** A component that prints whether the stream is open. */
  function Probe() { return <span id="probe">{useLive().connected ? "on" : "off"}</span>; }

  // One stream with nav + the page topic; connected after open; events fill the cache; a refused stream is retried after the back-off.
  it("opens one stream, applies events and reconnects", async () => {
    vi.useFakeTimers();
    const sources: FakeSource[] = [];
    const qc = new QueryClient();
    const { container } = await mount(
      <QueryClientProvider client={qc}><LiveEvents page="alarms" source={(u) => { const s = new FakeSource(u); sources.push(s); return s; }}><Probe /></LiveEvents></QueryClientProvider>,
    );
    expect(sources).toHaveLength(1);
    expect(sources[0].url).toBe("/api/events?topics=summary:nav,summary:alarms");
    await act(async () => { sources[0].open(); });
    expect(container.querySelector("#probe")?.textContent).toBe("on");
    await act(async () => { sources[0].emit("summary", { page: "nav", counts: { "alarms.critical": 4 }, changed: [] }); });
    expect(qc.getQueryData<{ counts: Record<string, number> }>(KEYS.summary("nav"))?.counts["alarms.critical"]).toBe(4);
    await act(async () => { sources[0].fail(); });
    expect(container.querySelector("#probe")?.textContent).toBe("off");
    await act(async () => { vi.advanceTimersByTime(1000); });
    expect(sources).toHaveLength(2);
  });

  // GUI-9.3 / 9.8b: under a scope the Dashboard's stream asks the scoped nav, dashboard and attention topics, and an attention event lands in the
  // scoped attention cache entry the "Needs your attention" card reads
  it("scopes the Dashboard's topics and applies attention events", async () => {
    const sources: FakeSource[] = [];
    const qc = new QueryClient();
    await mount(
      <QueryClientProvider client={qc}><ScopeContext.Provider value={{ scope: { region: "north", cluster: null }, setScope: () => {} }}>
        <LiveEvents page="dashboard" source={(u) => { const s = new FakeSource(u); sources.push(s); return s; }}><span /></LiveEvents>
      </ScopeContext.Provider></QueryClientProvider>,
    );
    expect(sources[0].url).toBe("/api/events?topics=summary:nav@north,summary:dashboard@north,summary:attention@north");
    await act(async () => { sources[0].open(); });
    await act(async () => { sources[0].emit("attention", { page: "attention", groups: [{ type: "approvals", total: 2, items: [] }], changed: ["approvals"],
      scope: { region: "north", siteCluster: null }, unscoped: ["mlmf-breaches"] }); });
    expect(qc.getQueryData<{ groups: { total: number }[]; unscoped: string[] }>(KEYS.attention("@north"))).toMatchObject({ groups: [{ total: 2 }], unscoped: ["mlmf-breaches"] });
  });

  // A page topic refused twice before the stream ever opened (a 403) falls back to the sidebar's topic alone; a disabled provider opens nothing.
  it("drops a refused page topic and opens nothing when disabled", async () => {
    vi.useFakeTimers();
    const sources: FakeSource[] = [];
    const make = (u: string) => { const s = new FakeSource(u); sources.push(s); return s; };
    await mount(<QueryClientProvider client={new QueryClient()}><LiveEvents page="aiml" source={make}><span /></LiveEvents></QueryClientProvider>);
    await act(async () => { sources[0].fail(); });
    await act(async () => { vi.advanceTimersByTime(1000); });
    await act(async () => { sources[1].fail(); });
    expect(sources.at(-1)!.url).toBe("/api/events?topics=summary:nav");
    const before = sources.length;
    await mount(<QueryClientProvider client={new QueryClient()}><LiveEvents page="alarms" disabled source={make}><span /></LiveEvents></QueryClientProvider>);
    expect(sources.length).toBe(before);
  });

  // The alarm sound preference beeps through WebAudio when critical alarms rise, not on the first reading.
  it("beeps when critical alarms rise and the sound is on", async () => {
    localStorage.setItem("smo.prefs", JSON.stringify({ alarmSound: true }));
    const started = vi.fn();
    class FakeAudio {
      currentTime = 0; destination = {};
      createGain() { return { gain: { value: 0 }, connect: () => {} }; }
      createOscillator() { return { frequency: { value: 0 }, connect: () => {}, start: started, stop: () => {}, onended: null }; }
      close() { return Promise.resolve(); }
    }
    vi.stubGlobal("AudioContext", FakeAudio);
    const sources: FakeSource[] = [];
    await mount(<QueryClientProvider client={new QueryClient()}><LiveEvents page={null} source={(u) => { const s = new FakeSource(u); sources.push(s); return s; }}><span /></LiveEvents></QueryClientProvider>);
    await act(async () => { sources[0].emit("summary", { page: "nav", counts: { "alarms.critical": 1 }, changed: [] }); });
    expect(started).not.toHaveBeenCalled();
    await act(async () => { sources[0].emit("summary", { page: "nav", counts: { "alarms.critical": 2 }, changed: ["alarms.critical"] }); });
    expect(started).toHaveBeenCalledTimes(2);
    localStorage.removeItem("smo.prefs");
    vi.unstubAllGlobals();
  });

  // The top bar says "pushed" while the stream is open and "polling" otherwise.
  it("switches the top bar chip", async () => {
    fakeBff({ ...ME, "GET /summary/nav": { page: "nav", computedAt: "", partial: [], counts: {} } });
    const sources: FakeSource[] = [];
    const { container } = await mount(
      <QueryClientProvider client={new QueryClient()}><MemoryRouter><AuthProvider><LiveEvents page={null} source={(u) => { const s = new FakeSource(u); sources.push(s); return s; }}><TopBar /></LiveEvents></AuthProvider></MemoryRouter></QueryClientProvider>,
    );
    await settle(4);
    expect(container.textContent).toContain("Live · polling");
    await act(async () => { sources[0].open(); });
    expect(container.textContent).toContain("Live · pushed");
  });
});

describe("server search", () => {
  // Server groups follow the page hits, titled by kind; with no answer the id jumps stand in.
  it("merges page jumps with the grouped answer", () => {
    const pages = [{ label: "Alarms", hint: "Page", to: "/alarms" }];
    const answer = { q: "du", partial: [], groups: [{ type: "element" as const, items: [{ id: "du-1", label: "du-1", hint: "east", to: "/elements/du-1" }] }] };
    expect(resultsFor("du", pages, answer).map((j) => [j.to, j.group])).toEqual([["/elements/du-1", "Managed elements"]]);
    expect(resultsFor("du-9", pages, undefined).map((j) => j.to)).toEqual(["/elements/du-9", "/alarms?me=du-9"]);
  });

  // Typing waits 200 ms, then asks /api/search once and shows the grouped results and the kinds that did not answer.
  it("asks the BFF after the debounce", async () => {
    const calls = fakeBff({
      ...ME,
      "GET /search": { q: "du-1", partial: ["model"], groups: [{ type: "element", items: [{ id: "du-1", label: "du-1", hint: "region east", to: "/elements/du-1" }] }] },
    });
    const { container } = await mountWith(<AuthProvider><GlobalSearch /></AuthProvider>);
    await settle(4);
    await type(container.querySelector("input")!, "du-1");
    expect(calls.filter((c) => c.path === "/search")).toHaveLength(0);
    await act(async () => { await new Promise((r) => setTimeout(r, 250)); });
    await settle(6);
    const search = calls.filter((c) => c.path === "/search");
    expect(search).toHaveLength(1);
    expect(search[0].query.get("q")).toBe("du-1");
    expect(container.textContent).toContain("Managed elements");
    expect(container.textContent).toContain("region east");
    expect(container.textContent).toContain("Not searched: AI/ML models");
  });
});
