/** The Account security page's one read: the signed-in user's one-time code status (`GET /api/me/totp`, PR-SEC-7.1). It uses the same query key as
 * `components/TotpEnrolment`, so the status tiles and the enrolment box share one call, and an enrolment refreshes both. */
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
