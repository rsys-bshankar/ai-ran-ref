/** The shell's one summary stream (SCALE.md P7; rules in `data/events.ts`). It opens `GET /api/events` for "summary:nav" plus the visible
 * page's summary, reopens it when the page changes, writes each event into the React Query cache, beeps when the number of critical alarms
 * rises and the user turned the alarm sound on (Preferences), and reconnects with a doubling back-off (1 s … 60 s) when the stream fails or
 * the BFF ends it (it does after 30 min). A hidden tab closes its stream, as a hidden tab stops polling, and opens it again when shown. If a
 * page's topic is refused twice before the stream ever opened (a 403 on a count the role cannot read), it falls back to "summary:nav" only until
 * the user opens another page. A user who must enrol a one-time code first gets no stream (`disabled`). */
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { applySummaryEvent, beep, browserSource, criticalRose, LiveContext, nextBackoff, streamUrl, topicsFor, type SourceFactory,
  type SummaryEvent } from "../data/events";
import type { SummaryPage } from "../data/summary";
import { usePreferences } from "./ThemeProvider";

/** The provider; `page` is the visible page's summary (null: none), `source` opens the stream (tests pass a fake), `disabled` opens none (a user
 * who must enrol a one-time code first: the BFF refuses every other route). */
export function LiveEvents({ page, source = browserSource, disabled = false, children }: { page: SummaryPage | null; source?: SourceFactory; disabled?: boolean; children: ReactNode }) {
  const qc = useQueryClient();
  const { prefs } = usePreferences();
  const soundRef = useRef(prefs.alarmSound);
  soundRef.current = prefs.alarmSound;
  const [connected, setConnected] = useState(false);
  const [visible, setVisible] = useState(() => typeof document === "undefined" || document.visibilityState !== "hidden");
  // the page whose topic the BFF refused on this visit (tried again when the user comes back to it)
  const [refused, setRefused] = useState<SummaryPage | null>(null);
  useEffect(() => { setRefused(null); }, [page]);
  const critical = useRef<number | null>(null);
  const topics = useMemo(() => topicsFor(page && refused !== page ? page : null), [page, refused]);
  const key = topics.join(",");

  useEffect(() => {
    const onVis = () => setVisible(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);

  useEffect(() => {
    if (!visible || disabled) { setConnected(false); return; }
    let es: ReturnType<SourceFactory> = null;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let backoff = 0;
    let failuresBeforeOpen = 0;
    let stopped = false;
    const open = () => {
      const seen = new Set<string>();
      let opened = false;
      es = source(streamUrl(topics));
      if (!es) return;
      es.onopen = () => { opened = true; backoff = 0; failuresBeforeOpen = 0; setConnected(true); };
      es.addEventListener("summary", (msg) => {
        let ev: SummaryEvent;
        try { ev = JSON.parse(String(msg.data)) as SummaryEvent; } catch { return; }
        if (!ev || typeof ev.page !== "string") return;
        opened = true;
        setConnected(true);
        applySummaryEvent(qc, ev, { first: !seen.has(ev.page) });
        seen.add(ev.page);
        const crit = ev.counts?.["alarms.critical"];
        if (typeof crit === "number") {
          if (criticalRose(critical.current, crit) && soundRef.current) beep();
          critical.current = crit;
        }
      });
      es.onerror = () => {
        setConnected(false);
        // EventSource retries a dropped connection itself (readyState CONNECTING); a refused one (4xx) is CLOSED and is ours to retry
        if (!es || es.readyState !== 2 || stopped) return;
        es.close();
        if (!opened) failuresBeforeOpen += 1;
        if (failuresBeforeOpen >= 2 && topics.length > 1 && page) { setRefused(page); return; }
        backoff = nextBackoff(backoff);
        timer = setTimeout(open, backoff);
      };
    };
    open();
    return () => { stopped = true; clearTimeout(timer); es?.close(); setConnected(false); };
    // `key` stands for `topics` (a new array each render); `page` only matters through it
  }, [key, visible, disabled, source, qc]);

  const value = useMemo(() => ({ connected }), [connected]);
  return <LiveContext.Provider value={value}>{children}</LiveContext.Provider>;
}
