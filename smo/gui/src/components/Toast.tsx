/**
 * The transient messages ("Pinned to the sidebar", "POST /x failed"): `ToastProvider` keeps the list and draws it in a polite live region (screen readers announce each
 * message), and `useToast().push` adds one. Used by the mutation hooks in `api/hooks.ts` and `api/rapps.ts` and by pages that call the BFF directly.
 */

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";

interface Toast { id: number; tone: "success" | "error" | "info"; text: string }
interface ToastApi { push: (t: Omit<Toast, "id">) => void }

const ToastContext = createContext<ToastApi | null>(null);
let nextId = 1;

/**
 * Provides `useToast` to the app and draws the toasts. Keeps the newest five; a message goes away by itself after 4 s (9 s for an error, so it can be read) or when clicked.
 */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const dismiss = useCallback((id: number) => setToasts((ts) => ts.filter((t) => t.id !== id)), []);
  const push = useCallback((t: Omit<Toast, "id">) => {
    const id = nextId++;
    setToasts((ts) => [...ts.slice(-4), { ...t, id }]);
    setTimeout(() => dismiss(id), t.tone === "error" ? 9000 : 4000);
  }, [dismiss]);
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

/** Returns the toast API (`push`); throws when used outside `ToastProvider`. */
export function useToast(): ToastApi {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error("useToast outside ToastProvider");
  return ctx;
}
