/** A KPI tile (`.kpi` in styles.css): label, big value, footer line. It replaces the pre-redesign `.stat` tile on every page
 * (BRIEF §3). A tile with `to` is a link, one with `onClick` a toggle (the Alarms severity filters); `tone` outlines a tile that
 * needs attention. A value the backend does not serve yet is passed as `null` and shows "—" (BRIEF §5: never invent a number). */
import type { ReactNode } from "react";
import { Link } from "react-router-dom";

/** Props of {@link Kpi}. `tone`: "hot" (red outline and fill), "warm" (amber outline), "volt" (accent outline). */
export interface KpiProps {
  label: ReactNode; value: ReactNode | null | undefined; unit?: ReactNode; foot?: ReactNode;
  tone?: "hot" | "warm" | "volt"; to?: string; onClick?: () => void; active?: boolean; title?: string; children?: ReactNode;
}

/** One KPI tile; `children` render under the footer (a meter or a sparkline). */
export function Kpi({ label, value, unit, foot, tone, to, onClick, active, title, children }: KpiProps) {
  const cls = `kpi${tone ? ` ${tone}` : ""}${active ? " on" : ""}`;
  const body = (
    <>
      <span className="kpi-l">{label}</span>
      <span className="kpi-v">{value ?? "—"}{unit && value != null && <small> {unit}</small>}</span>
      {foot && <span className="kpi-f">{foot}</span>}
      {children}
    </>
  );
  if (to) return <Link className={cls} to={to} title={title}>{body}</Link>;
  if (onClick) return <button type="button" className={cls} onClick={onClick} aria-pressed={active} title={title}>{body}</button>;
  return <div className={cls} title={title}>{body}</div>;
}

/** Formats a count for a tile or a badge: 1,234 below 10k, then 12.4k; past `cap` it reads "999+" (SCALE.md, sidebar badges). */
export function formatCount(n: number | null | undefined, cap?: number): string {
  if (n === null || n === undefined) return "—";
  if (cap !== undefined && n > cap) return `${cap}+`;
  if (n >= 10_000) return `${(n / 1000).toFixed(n >= 100_000 ? 0 : 1)}k`;
  return n.toLocaleString("en-US");
}
