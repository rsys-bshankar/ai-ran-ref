/** Status badges and severity chips (`.badge.b-*`, `.sev-*`). `toneOf` is the single table that maps a backend state name onto a tone;
 * `components/ui.tsx`'s `StateBadge` renders through it, so every page colours RUNNING, FAILED, PENDING… the same way. */
import type { ReactNode } from "react";

/** The tones a badge can take; `mute` is the neutral one. */
export type Tone = "ok" | "warn" | "bad" | "info" | "mute" | "volt";

/** A badge with a leading dot; `plain` hides the dot. */
export function Badge({ tone = "mute", plain, children, title }: { tone?: Tone; plain?: boolean; children: ReactNode; title?: string }) {
  return <span className={`badge b-${tone}${plain ? " plain" : ""}`} title={title}>{children}</span>;
}

const SEV: Record<string, string> = { CRITICAL: "cr", MAJOR: "mj", MINOR: "mn", WARNING: "wn", CLEARED: "cl", INDETERMINATE: "cl" };

/** The short class suffix of a perceived severity (TS 28.532): CRITICAL → "cr", unknown → "cl". */
export function sevClass(severity: string | null | undefined): string {
  return SEV[(severity ?? "").toUpperCase()] ?? "cl";
}
