import { useEffect, useState, type ReactNode } from "react";

import type { Query } from "../api/client";
import { useSmoAction, type SmoAction } from "../api/hooks";
import { useAuth } from "../auth/AuthContext";
import { shortId } from "../lib/domain";

// ---------------------------------------------------------------- layout bits

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        {subtitle && <p className="muted">{subtitle}</p>}
      </div>
      {actions && <div className="row gap">{actions}</div>}
    </header>
  );
}

export function Card({ title, actions, children, className = "" }: { title?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {(title || actions) && (
        <div className="card-head">
          {title && <h2>{title}</h2>}
          {actions && <div className="row gap">{actions}</div>}
        </div>
      )}
      {children}
    </section>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: ReactNode }[]; value: T; onChange: (t: T) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={value === t.id} className={value === t.id ? "tab active" : "tab"} onClick={() => onChange(t.id)}>
          {t.label}
        </button>
      ))}
    </div>
  );
}

/** A tab id kept in the URL hash, so reloads and shared links land on the same tab. */
export function useHashTab<T extends string>(ids: readonly T[], fallback: T): [T, (t: T) => void] {
  const read = () => {
    const h = window.location.hash.slice(1) as T;
    return ids.includes(h) ? h : fallback;
  };
  const [tab, setTab] = useState<T>(read);
  useEffect(() => {
    const onHash = () => setTab(read());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  });
  return [tab, (t: T) => { window.history.replaceState(null, "", `#${t}`); setTab(t); }];
}

// ---------------------------------------------------------------- status display

const TONES: Record<string, string> = {
  RUNNING: "ok", ACTIVE: "ok", AVAILABLE: "ok", PRIMED: "ok", ENFORCED: "ok", COMPLETED: "ok", RESOLVED: "ok",
  APPLIED: "ok", ACTIVATED: "ok", ENABLED: "ok", ACKNOWLEDGED: "ok", CERTIFIED: "info", LOADED: "info",
  DEPLOYING: "warn", TRAINING: "warn", TESTED: "info", EMULATED: "info", PRIMING: "warn", DEPRIMING: "warn",
  PENDING: "warn", PROCESSING: "warn", IN_PROGRESS: "warn", UPGRADING: "warn", INSTANTIATING: "warn",
  UPDATING: "warn", TERMINATING: "warn", DEGRADED: "warn", ONBOARDING: "warn", DISCOVERED: "info", PARTIAL_SUCCESS: "warn",
  FAILED: "bad", FAULTED: "bad", ABNORMAL: "bad", REJECTED: "bad", ESCALATED: "bad", UNREACHABLE: "bad",
  UNACKNOWLEDGED: "warn", DISABLED: "bad", DELETING: "muted", DEPRECATED: "muted", UNDEPLOYED: "muted",
  CANCELLED: "muted", DEACTIVATED: "muted", REGISTERED: "info", INITIAL: "info",
};

export function StateBadge({ state }: { state: string | null | undefined }) {
  if (!state) return <span className="muted">—</span>;
  return <span className={`badge tone-${TONES[state.toUpperCase()] ?? "muted"}`}>{state}</span>;
}

export function SeverityChip({ severity }: { severity: string }) {
  return <span className={`sev sev-${severity.toLowerCase()}`}>{severity}</span>;
}

export function Id({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="muted">—</span>;
  return (
    <code className="id" title={`${value} (click to copy)`} onClick={(e) => { e.stopPropagation(); navigator.clipboard?.writeText(value); }}>
      {shortId(value)}
    </code>
  );
}

export function KeyValue({ items }: { items: [ReactNode, ReactNode][] }) {
  return (
    <dl className="kv">
      {items.map(([k, v], i) => (
        <div key={i}><dt>{k}</dt><dd>{v ?? <span className="muted">—</span>}</dd></div>
      ))}
    </dl>
  );
}

export function Json({ value }: { value: unknown }) {
  return <pre className="json">{JSON.stringify(value, null, 2)}</pre>;
}

export function ErrorBox({ error }: { error: unknown }) {
  if (!error) return null;
  return <div className="error-box">{error instanceof Error ? error.message : String(error)}</div>;
}

// ---------------------------------------------------------------- table

export interface Column<T> { header: ReactNode; render: (row: T) => ReactNode; className?: string }

export function DataTable<T>({ rows, columns, rowKey, loading, error, empty = "Nothing here yet.", onRowClick, selectedKey }: {
  rows: T[] | undefined; columns: Column<T>[]; rowKey: (r: T) => string; loading?: boolean; error?: unknown;
  empty?: ReactNode; onRowClick?: (r: T) => void; selectedKey?: string | null;
}) {
  if (error) return <ErrorBox error={error} />;
  return (
    <div className="table-wrap">
      <table className="table">
        <thead><tr>{columns.map((c, i) => <th key={i} className={c.className}>{c.header}</th>)}</tr></thead>
        <tbody>
          {loading && !rows && <tr><td colSpan={columns.length} className="muted center">Loading…</td></tr>}
          {rows && rows.length === 0 && <tr><td colSpan={columns.length} className="muted center">{empty}</td></tr>}
          {rows?.map((r) => {
            const key = rowKey(r);
            return (
              <tr key={key} className={`${onRowClick ? "clickable" : ""} ${selectedKey === key ? "selected" : ""}`} onClick={onRowClick ? () => onRowClick(r) : undefined}>
                {columns.map((c, i) => <td key={i} className={c.className}>{c.render(r)}</td>)}
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------- overlays

export function Drawer({ title, onClose, children }: { title: ReactNode; onClose: () => void; children: ReactNode }) {
  useEscape(onClose);
  return (
    <div className="overlay" onClick={onClose}>
      <aside className="drawer" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head"><h2>{title}</h2><button className="btn ghost" onClick={onClose} aria-label="Close">✕</button></div>
        <div className="drawer-body">{children}</div>
      </aside>
    </div>
  );
}

export function Modal({ title, onClose, children }: { title: ReactNode; onClose: () => void; children: ReactNode }) {
  useEscape(onClose);
  return (
    <div className="overlay center-overlay" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className="drawer-head"><h2>{title}</h2><button className="btn ghost" onClick={onClose} aria-label="Close">✕</button></div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}

function useEscape(onClose: () => void) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
}

// ---------------------------------------------------------------- forms

export function Field({ label, hint, children }: { label: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}

// ---------------------------------------------------------------- role-gated actions

/** A button for one SMO lifecycle call. Rendered only when the BFF's
 * permission table lets the current role make that exact call; the BFF still
 * re-checks it. `confirm` asks before destructive calls. */
export function ActionButton({ action, label, tone = "default", confirm, disabled, onDone, title }: {
  action: SmoAction; label: ReactNode; tone?: "default" | "primary" | "danger"; confirm?: string;
  disabled?: boolean; onDone?: (data: unknown) => void; title?: string;
}) {
  const { can } = useAuth();
  const mutation = useSmoAction();
  if (!can(action.method, action.path, (action.query ?? {}) as Record<string, string>)) return null;
  const run = () => {
    if (confirm && !window.confirm(confirm)) return;
    mutation.mutate(action, { onSuccess: (d) => onDone?.(d) });
  };
  return (
    <button className={`btn ${tone}`} disabled={disabled || mutation.isPending} onClick={(e) => { e.stopPropagation(); run(); }} title={title}>
      {mutation.isPending ? "…" : label}
    </button>
  );
}

/** Children only when the role may make this call. */
export function Can({ method, path, query, children }: { method: string; path: string; query?: Query; children: ReactNode }) {
  const { can } = useAuth();
  return can(method, path, (query ?? {}) as Record<string, string>) ? <>{children}</> : null;
}
