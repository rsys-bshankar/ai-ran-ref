/** The top bar's search (⌘K / Ctrl+K focuses it). A client-side jump list for now (BRIEF §2: "it can start as a client-side jump"): every page
 * of the sidebar, every lifecycle flow board, and, for typed text that looks like an identifier, direct jumps to the decision record, the
 * managed element, the rApp instance, or the alarm list filtered to it. A server-side typeahead across DUs, cells, rApps, alarms and models is a
 * BFF ask (SCALE.md §5, item 3) and not built: nothing here claims to search data it has not got. */
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";
import { roleAtLeast } from "../auth/rbac";
import { FLOWS } from "../lib/flows";
import { Icon } from "../kit/icons";
import { NAV } from "./nav";

/** One entry of the jump list. */
export interface Jump { label: string; hint: string; to: string }

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** The jumps for `text`: matching pages and flows, then identifier jumps. At most 8. */
export function jumpsFor(text: string, pages: Jump[]): Jump[] {
  const q = text.trim().toLowerCase();
  if (!q) return pages.slice(0, 8);
  const hits = pages.filter((p) => p.label.toLowerCase().includes(q) || p.hint.toLowerCase().includes(q));
  const raw = text.trim();
  const ids: Jump[] = [];
  if (UUID.test(raw)) {
    ids.push({ label: `Decision record ${raw.slice(0, 8)}…`, hint: "Decisions", to: `/decisions/${raw}` });
    ids.push({ label: `rApp instance ${raw.slice(0, 8)}…`, hint: "rApps", to: `/rapps/${raw}` });
  } else if (raw.length >= 2 && !/\s/.test(raw)) {
    ids.push({ label: `Managed element ${raw}`, hint: "Element detail", to: `/elements/${encodeURIComponent(raw)}` });
    ids.push({ label: `Alarms on ${raw}`, hint: "Alarms", to: `/alarms?me=${encodeURIComponent(raw)}` });
  }
  return [...hits, ...ids].slice(0, 8);
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
  const results = useMemo(() => jumpsFor(text, pages), [text, pages]);

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
        <input ref={input} type="search" aria-label="Search" placeholder="Jump to a page, flow, element, rApp or decision id…" value={text}
          role="combobox" aria-expanded={open} aria-controls="global-search-results" aria-autocomplete="list"
          onChange={(e) => { setText(e.target.value); setCursor(0); setOpen(true); }} onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)} onKeyDown={onKey} />
        <kbd>⌘K</kbd>
      </label>
      {open && results.length > 0 && (
        <ul className="search-results" id="global-search-results" role="listbox">
          {results.map((r, i) => (
            <li key={r.to + r.label} className={i === cursor ? "on" : ""} role="option" aria-selected={i === cursor}>
              <button type="button" onMouseDown={(e) => { e.preventDefault(); go(r); }}>
                <span className="grow">{r.label}</span><span className="xs muted">{r.hint}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
