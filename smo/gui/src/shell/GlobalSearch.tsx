/** The top bar's search (⌘K / Ctrl+K focuses it). Two parts, in one keyboard list: the client-side jumps (every page of the sidebar, every
 * lifecycle flow board) and, for typed text of 2 characters or more, the BFF's cross-object typeahead `GET /api/search?q=&limit=5`
 * (gui-bff/app/search.py: managed elements, rApps, alarms by element, AI/ML models, a decision record by id), asked 200 ms after the last key and
 * shown grouped by kind. A kind whose module did not answer is named under the list ("not searched: …"). While the server answer is missing
 * (loading, failed, or before 2 characters) the identifier jumps stand in: a UUID offers the decision record and the rApp instance, a single
 * token the managed element and its alarms. */
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { api, ApiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { roleAtLeast } from "../auth/rbac";
import { FLOWS } from "../lib/flows";
import { Icon } from "../kit/icons";
import { NAV } from "./nav";

/** One entry of the jump list (`group` titles the server's kinds). */
export interface Jump { label: string; hint: string; to: string; group?: string }

/** The kinds `GET /api/search` groups by. */
export type SearchKind = "element" | "rapp" | "alarm" | "model" | "decision";

/** The body of `GET /api/search`. */
export interface SearchAnswer { q: string; groups: { type: SearchKind; items: { id: string; label: string; hint?: string | null; to: string }[] }[]; partial: SearchKind[] }

/** The title of each kind's group. */
export const KIND_TITLE: Record<SearchKind, string> = { element: "Managed elements", rapp: "rApps", alarm: "Alarms", model: "AI/ML models", decision: "Decision records" };

/** The server is asked once the text is this long (the BFF answers 400 below it). */
export const MIN_SERVER_QUERY = 2;
/** The server is asked this long after the last key. */
export const SEARCH_DEBOUNCE_MS = 200;

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The identifier jumps for `text` (a UUID: decision record and rApp instance; one token: managed element and its alarms). */
export function idJumps(text: string): Jump[] {
  const raw = text.trim();
  if (UUID.test(raw)) {
    return [{ label: `Decision record ${raw.slice(0, 8)}…`, hint: "Decisions", to: `/decisions/${raw}` },
      { label: `rApp instance ${raw.slice(0, 8)}…`, hint: "rApps", to: `/rapps/${raw}` }];
  }
  if (raw.length >= 2 && !/\s/.test(raw)) {
    return [{ label: `Managed element ${raw}`, hint: "Element detail", to: `/elements/${encodeURIComponent(raw)}` },
      { label: `Alarms on ${raw}`, hint: "Alarms", to: `/alarms?me=${encodeURIComponent(raw)}` }];
  }
  return [];
}

/** The jumps for `text` without a server answer: matching pages and flows, then identifier jumps. At most 8. */
export function jumpsFor(text: string, pages: Jump[]): Jump[] {
  const q = text.trim().toLowerCase();
  if (!q) return pages.slice(0, 8);
  const hits = pages.filter((p) => p.label.toLowerCase().includes(q) || p.hint.toLowerCase().includes(q));
  return [...hits, ...idJumps(text)].slice(0, 8);
}

/** The whole list for `text`: up to 5 matching pages and flows, then the server's groups (each item titled by its kind); with no server
 * answer, the identifier jumps instead. */
export function resultsFor(text: string, pages: Jump[], answer: SearchAnswer | undefined): Jump[] {
  if (!answer) return jumpsFor(text, pages);
  const q = text.trim().toLowerCase();
  const hits = pages.filter((p) => p.label.toLowerCase().includes(q) || p.hint.toLowerCase().includes(q)).slice(0, 5);
  const found = answer.groups.flatMap((g) => g.items.map((i) => ({ label: i.label, hint: i.hint ?? "", to: i.to, group: KIND_TITLE[g.type] ?? g.type })));
  return [...hits, ...(found.length > 0 ? found : idJumps(text))];
}

/** `value`, once it has stopped changing for `ms`. */
export function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** The BFF's grouped answer for `q` (no call below {@link MIN_SERVER_QUERY} characters; answers are kept 10 s, as the BFF caches them). */
export function useServerSearch(q: string) {
  const text = q.trim();
  return useQuery<SearchAnswer, ApiError>({
    queryKey: ["bff", "search", text],
    queryFn: ({ signal }) => api<SearchAnswer>("/search", { query: { q: text, limit: 5 }, signal }),
    enabled: text.length >= MIN_SERVER_QUERY,
    staleTime: 10_000,
    retry: false,
  });
}

/** The search box and its result list. */
export function GlobalSearch() {
  const { me } = useAuth();
  const navigate = useNavigate();
  const input = useRef<HTMLInputElement>(null);
  const [text, setText] = useState("");
  const [open, setOpen] = useState(false);
  const [cursor, setCursor] = useState(0);
  const pages = useMemo<Jump[]>(() => [
    ...NAV.filter((n) => !n.minRole || (me && roleAtLeast(me.role, n.minRole))).map((n) => ({ label: n.label, hint: "Page", to: n.to })),
    ...FLOWS.map((f) => ({ label: `Flow ${f.id} · ${f.title}`, hint: "Lifecycle flow", to: `/flows/${f.id}` })),
  ], [me]);
  const debounced = useDebounced(text, SEARCH_DEBOUNCE_MS);
  const server = useServerSearch(debounced);
  const answer = debounced.trim() === text.trim() && text.trim().length >= MIN_SERVER_QUERY ? server.data : undefined;
  const results = useMemo(() => resultsFor(text, pages, answer), [text, pages, answer]);

  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") { e.preventDefault(); input.current?.focus(); setOpen(true); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const go = (j: Jump | undefined) => {
    if (!j) return;
    navigate(j.to);
    setText(""); setOpen(false); input.current?.blur();
  };
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setCursor((c) => Math.min(c + 1, results.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setCursor((c) => Math.max(c - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); go(results[cursor]); }
    else if (e.key === "Escape") { setOpen(false); input.current?.blur(); }
  };
  return (
    <div className="topbar-search">
      <label className="search">
        <Icon name="search" size={16} />
        <input ref={input} type="search" aria-label="Search" placeholder="Search elements, rApps, alarms, models, decisions or pages…" value={text}
          role="combobox" aria-expanded={open} aria-controls="global-search-results" aria-autocomplete="list"
          onChange={(e) => { setText(e.target.value); setCursor(0); setOpen(true); }} onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)} onKeyDown={onKey} />
        <kbd>⌘K</kbd>
      </label>
      {open && results.length > 0 && (
        <ul className="search-results" id="global-search-results" role="listbox">
          {results.map((r, i) => (
            <SearchRow key={`${r.group ?? ""}${r.to}${r.label}`} jump={r} on={i === cursor} first={!!r.group && results[i - 1]?.group !== r.group} onGo={go} />
          ))}
          {server.isFetching && <li className="xs muted search-note" aria-live="polite">Searching…</li>}
          {answer && answer.partial.length > 0 && <li className="xs muted search-note">Not searched: {answer.partial.map((k) => KIND_TITLE[k] ?? k).join(", ")} did not answer.</li>}
          {server.isError && <li className="xs muted search-note">Search unavailable ({server.error.message}); showing jumps only.</li>}
        </ul>
      )}
    </div>
  );
}

/** One row of the list; the first row of a server kind carries the kind's title above it. */
function SearchRow({ jump, on, first, onGo }: { jump: Jump; on: boolean; first: boolean; onGo: (j: Jump) => void }) {
  return (
    <li className={on ? "on" : ""} role="option" aria-selected={on}>
      {first && <span className="eyebrow search-group">{jump.group}</span>}
      <button type="button" onMouseDown={(e) => { e.preventDefault(); onGo(jump); }}>
        <span className="grow">{jump.label}</span><span className="xs muted">{jump.hint}</span>
      </button>
    </li>
  );
}
