/**
 * The react-query hooks every page reads and writes SMO modules through: `useSmo` and `useSmoPage` (GET one module path via the BFF's /smo proxy,
 * cached by path and query so pages share entries, polled every `POLL.lists`) and `useSmoAction` (a lifecycle POST/PUT/PATCH/DELETE with a toast).
 * Built on `client.ts`; the caches it uses are keyed ["smo", ...] (module reads) and ["bff", ...] (BFF reads such as the user and the pins),
 * and a successful action invalidates both, because one module's change routinely alters another's state.
 */

import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from "@tanstack/react-query";

import { useToast } from "../components/Toast";
import { ApiError, smo, type Query } from "./client";
import type { Page } from "./types";

// Polling cadences (ms): fast-moving operational state vs slower inventory.
export const POLL = { alarms: 5_000, status: 10_000, lists: 15_000 } as const;

/** Wave 3: every list-returning GET across the SMO backend now answers
 * {items, total, limit, offset} instead of a bare array (real limit/offset
 * pagination, confirmed as a breaking change rather than left undone). No
 * endpoint in this build otherwise shapes a response as {items: [...]}, so
 * unwrapping it here — once, at the fetch boundary — keeps every existing
 * call site's own T[] type and array usage (.map/.filter/.length) exactly
 * as it was, instead of touching ~90 call sites across the GUI. */
export function unwrapPage<T>(data: unknown): T {
  if (data && typeof data === "object" && Array.isArray((data as { items?: unknown }).items)) {
    return (data as { items: T }).items;
  }
  return data as T;
}

/** GET one SMO module path through the BFF. Keyed by path + query, so any
 * page reading the same resource shares one cache entry. */
export function useSmo<T>(path: string | null, query?: Query, opts: Partial<UseQueryOptions<T, ApiError>> = {}) {
  return useQuery<T, ApiError>({
    queryKey: ["smo", path, query ?? {}],
    queryFn: async ({ signal }) => unwrapPage<T>(await smo<unknown>(path!, { query, signal })),
    enabled: path !== null && (opts.enabled ?? true),
    refetchInterval: POLL.lists,
    ...opts,
  });
}

/** GET a list and keep its envelope (`total` or `hasMore`), for a page that pages: `useSmo` unwraps it to the bare array. */
export function useSmoPage<T>(path: string | null, query?: Query, opts: Partial<UseQueryOptions<Page<T>, ApiError>> = {}) {
  return useQuery<Page<T>, ApiError>({
    queryKey: ["smo", path, "page", query ?? {}],
    queryFn: ({ signal }) => smo<Page<T>>(path!, { query, signal }),
    enabled: path !== null && (opts.enabled ?? true),
    refetchInterval: POLL.lists,
    ...opts,
  });
}

/**
 * One lifecycle call for `useSmoAction`: the HTTP method, the path under /smo, optional query and JSON or raw body, and the toast text shown on success.
 */
export interface SmoAction {
  method: "POST" | "PUT" | "PATCH" | "DELETE";
  path: string;
  query?: Query;
  json?: unknown;
  body?: BodyInit;
  /** Toast text on success; omit for silent actions. */
  success?: string;
}

/** Every lifecycle action goes through here: call, toast, then refetch all
 * SMO reads (a mutation in one module routinely changes another's state,
 * e.g. CreateInstance -> NFO deployment + Onboarding usage). */
export function useSmoAction() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation<unknown, ApiError, SmoAction>({
    mutationFn: (a) => smo(a.path, { method: a.method, query: a.query, json: a.json, body: a.body }),
    onSuccess: (_data, a) => {
      if (a.success) toast.push({ tone: "success", text: a.success });
      qc.invalidateQueries({ queryKey: ["smo"] });
      qc.invalidateQueries({ queryKey: ["bff"] });
    },
    onError: (err, a) => toast.push({ tone: "error", text: `${a.method} ${a.path} failed — ${err.message}` }),
  });
}
