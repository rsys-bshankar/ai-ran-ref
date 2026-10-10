import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from "@tanstack/react-query";

import { useToast } from "../components/Toast";
import { invalidateAfter } from "../data/keys";
import { scopeQuery, useScope } from "../data/scope";
import { ApiError, smo, type Query } from "./client";
import type { Page } from "./types";

// Polling cadences (ms): fast-moving operational state vs slower inventory. A hidden tab does not poll (React Query's default,
// refetchIntervalInBackground: false), and tiles and badges read one cached summary call (data/summary.ts) instead of polling lists (SCALE.md §1.4).
export const POLL = { alarms: 5_000, status: 10_000, lists: 15_000, summary: 15_000, inventory: 60_000 } as const;

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
 * page reading the same resource shares one cache entry. A route the global scope narrows (`data/scope.ts` SCOPED_ROUTES) gets the
 * scope's `region` / `site_cluster` added, unless the caller set them (GUI-9.3). */
export function useSmo<T>(path: string | null, query?: Query, opts: Partial<UseQueryOptions<T, ApiError>> = {}) {
  query = scopeQuery(path, query, useScope());
  return useQuery<T, ApiError>({
    queryKey: ["smo", path, query ?? {}],
    queryFn: async ({ signal }) => unwrapPage<T>(await smo<unknown>(path!, { query, signal })),
    enabled: path !== null && (opts.enabled ?? true),
    refetchInterval: POLL.lists,
    ...opts,
  });
}

/** GET a list and keep its envelope (`total` or `hasMore`), for a page that pages: `useSmo` unwraps it to the bare array. Scoped as `useSmo`. */
export function useSmoPage<T>(path: string | null, query?: Query, opts: Partial<UseQueryOptions<Page<T>, ApiError>> = {}) {
  query = scopeQuery(path, query, useScope());
  return useQuery<Page<T>, ApiError>({
    queryKey: ["smo", path, "page", query ?? {}],
    queryFn: ({ signal }) => smo<Page<T>>(path!, { query, signal }),
    enabled: path !== null && (opts.enabled ?? true),
    refetchInterval: POLL.lists,
    ...opts,
  });
}

export interface SmoAction {
  method: "POST" | "PUT" | "PATCH" | "DELETE";
  path: string;
  query?: Query;
  json?: unknown;
  body?: BodyInit;
  /** Toast text on success; omit for silent actions. */
  success?: string;
  /** More modules whose reads this action can make stale, beyond its own and the ones `data/keys.ts` CROSS_MODULE lists. */
  invalidates?: string[];
}

/** Every lifecycle action goes through here: call, toast, then refetch the reads it can have changed: its own module's, those of the
 * modules a call there is known to change too (CreateInstance -> NFO deployment + Onboarding usage, `data/keys.ts`), and the summary
 * counts (SCALE.md P8; the pre-redesign GUI refetched every read in the tab on every action). */
export function useSmoAction() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation<unknown, ApiError, SmoAction>({
    mutationFn: (a) => smo(a.path, { method: a.method, query: a.query, json: a.json, body: a.body }),
    onSuccess: (_data, a) => {
      if (a.success) toast.push({ tone: "success", text: a.success });
      void invalidateAfter(qc, a.path, a.invalidates);
    },
    onError: (err, a) => toast.push({ tone: "error", text: `${a.method} ${a.path} failed — ${err.message}` }),
  });
}
