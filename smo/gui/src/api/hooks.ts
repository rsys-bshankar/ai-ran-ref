import { useMutation, useQuery, useQueryClient, type UseQueryOptions } from "@tanstack/react-query";

import { useToast } from "../components/Toast";
import { ApiError, smo, type Query } from "./client";

// Polling cadences (ms): fast-moving operational state vs slower inventory.
export const POLL = { alarms: 5_000, status: 10_000, lists: 15_000 } as const;

/** GET one SMO module path through the BFF. Keyed by path + query, so any
 * page reading the same resource shares one cache entry. */
export function useSmo<T>(path: string | null, query?: Query, opts: Partial<UseQueryOptions<T, ApiError>> = {}) {
  return useQuery<T, ApiError>({
    queryKey: ["smo", path, query ?? {}],
    queryFn: ({ signal }) => smo<T>(path!, { query, signal }),
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
