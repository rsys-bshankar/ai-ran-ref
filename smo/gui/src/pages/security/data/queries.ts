/** The Account security page's reads: the signed-in user's one-time code status (`GET /api/me/totp`, PR-SEC-7.1, with its recovery-code slots),
 * which uses the same query key as `components/TotpEnrolment` so the status tiles and the enrolment box share one call and an enrolment refreshes
 * both; and the user's own recent sign-ins (`GET /api/me/sign-ins?limit=20`, GUI-9.8). */
import { useQuery } from "@tanstack/react-query";

import { api, ApiError } from "../../../api/client";
import type { TotpStatus } from "../../../api/types";

/** The query key `components/TotpEnrolment` uses; kept equal so the cache is shared. */
export const TOTP_KEY = ["bff", "totp"] as const;

/** Whether a one-time code is set up, how many recovery codes are left, and why codes are unavailable (`reason`). Not retried: an identity-provider
 * user or a server without `GUI_TOTP_KEY` answers an error the page shows as it is. */
export function useTotpStatus() {
  return useQuery<TotpStatus, ApiError>({ queryKey: TOTP_KEY, queryFn: () => api<TotpStatus>("/me/totp"), retry: false });
}

/** One row of `GET /api/me/sign-ins`: a sign-in, a failed one, a sign-out or a token grant of the signed-in user (never another user's). */
export interface SignIn { at: string; action: string; detail: string | null }

/** How many sign-ins the page lists. */
export const SIGN_IN_LIMIT = 20;

/** The signed-in user's newest sign-ins, newest first. */
export function useSignIns() {
  return useQuery<SignIn[], ApiError>({ queryKey: ["bff", "sign-ins"], queryFn: ({ signal }) => api<SignIn[]>("/me/sign-ins", { query: { limit: SIGN_IN_LIMIT }, signal }),
    retry: false, staleTime: 30_000 });
}
