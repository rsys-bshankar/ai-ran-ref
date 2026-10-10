/** Every API path the Admin page uses. GUI users and the audit log live in the BFF (`/api/admin/users`, `/api/admin/audit`, admin only); the RAN
 * access control (MSAC) lists are RAN NF OAM's (`/ran-nf-oam/msac/...`, read through the BFF proxy by `kit/ServerTable`). */
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError, type RequestOptions } from "../../../api/client";
import type { AuditEntry, GuiUser, Page } from "../../../api/types";
import { useToast } from "../../../components/Toast";

/** The MSAC list routes (ServerTable paths) and the item path a delete goes to. */
export const MSAC = {
  roles: "/ran-nf-oam/msac/roles",
  identities: "/ran-nf-oam/msac/identities",
  rules: "/ran-nf-oam/msac/access-rules",
} as const;

/** The audit actions the BFF writes (`GET /api/admin/audit/actions`, gui-bff/app/main.py `AUDIT_ACTIONS`, GUI-10.4), offered as the action filter;
 * the BFF's test fails when its code writes an action the list lacks, so the filter cannot drift from the log. */
export function useAuditActions() {
  return useQuery<{ actions: string[] }, ApiError>({ queryKey: ["bff", "admin", "audit-actions"], queryFn: ({ signal }) => api("/admin/audit/actions", { signal }), staleTime: 3_600_000 });
}

/** Every GUI user. The BFF answers the whole list (no paging: users number in the tens to hundreds). */
export function useAdminUsers() {
  return useQuery<GuiUser[], ApiError>({ queryKey: ["bff", "admin", "users"], queryFn: () => api("/admin/users") });
}

/** The filters and page of the audit log (`GET /api/admin/audit?username&action&since&until&limit&after_id`, newest first, keyset-paged: `afterId`
 * null is the newest page, else the `nextAfterId` of the page before). */
export interface AuditQuery { username: string; action: string; since: string | null; until: string | null; limit: number; afterId: number | null }

/** One audit page: the rows, its total and the id to ask the next (older) page after (null on the last page). */
export type AuditPage = Page<AuditEntry> & { nextAfterId?: number | null };

/** The filters of an audit query as route parameters (no paging). */
export function auditFilters(q: Pick<AuditQuery, "username" | "action" | "since" | "until">): Record<string, string | undefined> {
  return { username: q.username || undefined, action: q.action || undefined, since: q.since || undefined, until: q.until || undefined };
}

/** One page of the audit log, refreshed every 10 s while it is the newest page; the previous page stays on screen while the next loads. */
export function useAuditPage(q: AuditQuery) {
  return useQuery<AuditPage, ApiError>({
    queryKey: ["bff", "admin", "audit", q],
    queryFn: ({ signal }) => api<AuditPage>("/admin/audit", { query: { ...auditFilters(q), limit: q.limit, after_id: q.afterId ?? undefined }, signal }),
    refetchInterval: q.afterId === null ? 10_000 : false,
    placeholderData: keepPreviousData,
  });
}

/** A BFF admin call (create, patch, delete a user …): toast the outcome and refetch the admin reads. */
export function useBffMutation() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation<unknown, ApiError, { path: string; opts: RequestOptions; success: string }>({
    mutationFn: ({ path, opts }) => api(path, opts),
    onSuccess: (_d, v) => { toast.push({ tone: "success", text: v.success }); qc.invalidateQueries({ queryKey: ["bff", "admin"] }); },
    onError: (e) => toast.push({ tone: "error", text: e.message }),
  });
}
