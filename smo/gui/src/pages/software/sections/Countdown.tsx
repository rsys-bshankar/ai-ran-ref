/** A live countdown to a moment ("starts in 06:12"), re-rendered every second; "now" once it has passed. Used by the Software page for the
 * pause between campaign waves and by the Configuration page for the pause between config-job waves. */
import { useEffect, useState } from "react";

/** "mm:ss" (or "h:mm:ss") until `until`, or "now". */
export function formatLeft(until: string, now: number): string {
  const left = Math.max(0, Math.round((new Date(until).getTime() - now) / 1000));
  if (left === 0) return "now";
  const h = Math.floor(left / 3600), m = Math.floor((left % 3600) / 60), s = left % 60;
  const mm = String(m).padStart(2, "0"), ss = String(s).padStart(2, "0");
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

/** The ticking text. */
export function Countdown({ until }: { until: string }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);
  return <time className="mono" dateTime={until} title={new Date(until).toLocaleString()}>{formatLeft(until, now)}</time>;
}
