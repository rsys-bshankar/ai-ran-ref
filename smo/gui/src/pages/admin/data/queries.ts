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

/** The audit events the BFF writes (gui-bff/app/main.py `_audit` callers), offered as the action filter. */
export const AUDIT_ACTIONS = ["PROXY", "DENIED", "LOGIN", "LOGIN_FAILED", "LOGIN_LOCKED", "LOGIN_REFUSED", "MFA_CHALLENGE", "BREAK_GLASS_LOGIN",
  "RECOVERY_CODE_USED", "LOGOUT", "TOKEN", "PASSWORD_CHANGED", "TOTP_ENROL_STARTED", "TOTP_ENROLLED", "TOTP_RESET", "RECOVERY_CODES_REGENERATED",
  "USER_CREATED", "USER_UPDATED", "USER_DELETED", "USER_SESSIONS_REVOKED"] as const;

/** Every GUI user. The BFF answers the whole list (no paging: users number in the tens to hundreds). */
export function useAdminUsers() {
  return useQuery<GuiUser[], ApiError>({ queryKey: ["bff", "admin", "users"], queryFn: () => api("/admin/users") });
}

/** The filters and page of the audit log (`GET /api/admin/audit?username&action&limit&offset`, newest first). */
export interface AuditQuery { username: string; action: string; limit: number; offset: number }

/** One page of the audit log with its total, refreshed every 10 s; the previous page stays on screen while the next loads. */
export function useAuditPage(q: AuditQuery) {
  return useQuery<Page<AuditEntry>, ApiError>({
    queryKey: ["bff", "admin", "audit", q],
    queryFn: ({ signal }) => api<Page<AuditEntry>>("/admin/audit", { query: { limit: q.limit, offset: q.offset, username: q.username || undefined, action: q.action || undefined }, signal }),
    refetchInterval: 10_000,
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
