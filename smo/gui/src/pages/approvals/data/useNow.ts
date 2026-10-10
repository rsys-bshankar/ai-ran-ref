/** A clock for the Approvals page's lapse countdowns: the current time, re-read every `everyMs` while the component is mounted. */
import { useEffect, useState } from "react";

/** The current time in ms, refreshed every `everyMs`. */
export function useNow(everyMs: number): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), everyMs);
    return () => clearInterval(t);
  }, [everyMs]);
  return now;
}
