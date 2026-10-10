/** A callout (`.callout`): attention items, a root-cause hint, a halted job's reason, a data-gap note. */
import type { ReactNode } from "react";

/** The callout; `tone` sets the outline (bad, warn, volt, info), `icon` an optional leading SVG. */
export function Callout({ tone, title, children, icon, actions }: { tone?: "bad" | "warn" | "volt" | "info"; title?: ReactNode; children?: ReactNode; icon?: ReactNode; actions?: ReactNode }) {
  return (
    <div className={`callout${tone ? ` ${tone}` : ""}`} role={tone === "bad" ? "alert" : undefined}>
      {icon}
      <div className="col grow" style={{ gap: 4 }}>
        {title && <strong>{title}</strong>}
        {children && <div className="small muted">{children}</div>}
      </div>
      {actions && <div className="row">{actions}</div>}
    </div>
  );
}
