// The BFF's rApp routes (gui-bff/app/rapps.py, PR-GUI-8): the directory, one rApp with its declared page, the declared routes, the user's pins.
// All keys start with "bff" so the existing `useSmoAction` invalidation (["bff"]) also refreshes them.

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useToast } from "../components/Toast";
import { refreshInterval, type Obj } from "../lib/operatorUi";
import { api, ApiError, type Query } from "./client";

export interface RappSummary {
  instanceId: string; packageId: string; name: string | null; version: string | null; vendor: string | null;
  state: string | null; autonomyMode: string | null; hasPage: boolean; operatorApiRegistered: boolean; pinned: boolean;
}

export interface RappDirectory { items: RappSummary[]; total: number; limit: number; offset: number; owners: string[]; states: string[] }

export interface RappPage extends RappSummary {
  declarationState: "declared" | "none" | "unreadable";
  declaration: Declaration | null;
  readOnly: boolean;
  /** The user's role may change things AND the page is not read-only: the renderer draws the change buttons only then. */
  canChange: boolean;
}

// The declaration as the renderer reads it. Loose on purpose (see lib/operatorUi.ts): the stored one may be newer than this build.
export interface Declaration { version?: number; readOnly?: boolean; panels: Obj[] }

export interface DirectoryQuery { search?: string; state?: string; owner?: string; hasPage?: boolean | ""; pinned?: boolean | ""; limit?: number; offset?: number }

export function useRappDirectory(q: DirectoryQuery) {
  return useQuery<RappDirectory, ApiError>({
    queryKey: ["bff", "rapps", q],
    queryFn: ({ signal }) => api<RappDirectory>("/rapps", { query: { ...q, hasPage: q.hasPage === "" ? undefined : q.hasPage, pinned: q.pinned === "" ? undefined : q.pinned } as Query, signal }),
    placeholderData: keepPreviousData,
    refetchInterval: 15_000,
  });
}

export function useRapp(instanceId: string) {
  return useQuery<RappPage, ApiError>({
    queryKey: ["bff", "rapp", instanceId],
    queryFn: ({ signal }) => api<RappPage>(`/rapps/${instanceId}`, { signal }),
    refetchInterval: 15_000,
  });
}

// ---------------------------------------------------------------- the declared routes

/** A declared read through the BFF. `path` is the route relative to the rApp's operator API (already filled); `seconds` is the source's refreshSeconds. */
export function useOperatorRead(instanceId: string, path: string | null, query: Query | null, seconds: unknown, enabled = true) {
  return useQuery<unknown, ApiError>({
    queryKey: ["bff", "rapp-op", instanceId, path, query ?? {}],
    queryFn: ({ signal }) => api<unknown>(`/rapps/${instanceId}/operator${path}`, { query: query ?? undefined, signal }),
    enabled: enabled && path !== null && query !== null,
    refetchInterval: refreshInterval(seconds),
  });
}

export interface OperatorAction { actionId: string; method: "POST" | "PUT" | "PATCH" | "DELETE"; path: string; body?: Obj; success: string }

export function useOperatorAction(instanceId: string) {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation<unknown, ApiError, OperatorAction>({
    mutationFn: (a) => api(`/rapps/${instanceId}/operator${a.path}`, { method: a.method, json: a.method === "DELETE" ? undefined : a.body ?? {}, headers: { "X-Action-Id": a.actionId } }),
    onSuccess: (_data, a) => {
      toast.push({ tone: "success", text: a.success });
      qc.invalidateQueries({ queryKey: ["bff", "rapp-op", instanceId] });
      qc.invalidateQueries({ queryKey: ["smo"] });
    },
    onError: (err, a) => toast.push({ tone: "error", text: `${a.actionId} failed — ${err.message}` }),
  });
}

// ---------------------------------------------------------------- pins (the sidebar)

export const MAX_PINS = 5;

export interface Pins { max: number; items: RappSummary[] }

export function usePins(enabled = true) {
  return useQuery<Pins, ApiError>({ queryKey: ["bff", "pins"], queryFn: ({ signal }) => api<Pins>("/me/pins", { signal }), enabled, staleTime: 30_000, retry: false });
}

export function usePinToggle() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation<unknown, ApiError, { instanceId: string; pin: boolean }>({
    mutationFn: ({ instanceId, pin }) => api(`/me/pins/${instanceId}`, { method: pin ? "PUT" : "DELETE" }),
    onSuccess: (_d, { pin }) => {
      toast.push({ tone: "success", text: pin ? "Pinned to the sidebar" : "Unpinned" });
      qc.invalidateQueries({ queryKey: ["bff"] });
    },
    onError: (err) => toast.push({ tone: "error", text: err.status === 409 ? `At most ${MAX_PINS} rApps can be pinned: unpin one first` : `Pinning failed — ${err.message}` }),
  });
}
