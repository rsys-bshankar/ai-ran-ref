/** The console's toasts: short messages in the corner (success 4 s, error 9 s, or until clicked), at most five at once. Each one's dismiss timer
 * is tracked and cleared when the toast is dismissed early or the provider unmounts (GUI-10.10), so no timer fires into an unmounted tree. */
import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

interface Toast { id: number; tone: "success" | "error" | "info"; text: string }
interface ToastApi { push: (t: Omit<Toast, "id">) => void }

const ToastContext = createContext<ToastApi | null>(null);
let nextId = 1;

/** How long a toast stays, by tone (ms). */
export const TOAST_MS = { error: 9000, other: 4000 } as const;

/** The provider and the toast stack it draws. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>());
  const dismiss = useCallback((id: number) => {
    const timer = timers.current.get(id);
    if (timer !== undefined) clearTimeout(timer);
    timers.current.delete(id);
    setToasts((ts) => ts.filter((t) => t.id !== id));
  }, []);
  const push = useCallback((t: Omit<Toast, "id">) => {
    const id = nextId++;
    setToasts((ts) => [...ts.slice(-4), { ...t, id }]);
    timers.current.set(id, setTimeout(() => dismiss(id), t.tone === "error" ? TOAST_MS.error : TOAST_MS.other));
  }, [dismiss]);
  useEffect(() => {
    const pending = timers.current;
    return () => { pending.forEach((timer) => clearTimeout(timer)); pending.clear(); };
  }, []);
  const api = useMemo(() => ({ push }), [push]);
  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.tone}`} onClick={() => dismiss(t.id)}>{t.text}</div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

/** The toasts of the nearest provider; throws outside one (a page always has one). */
export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast outside ToastProvider");
  return ctx;
}

/** The toasts, or null outside a provider (the session provider, which some tests mount on its own). */
export function useOptionalToast(): ToastApi | null {
  return useContext(ToastContext);
}
