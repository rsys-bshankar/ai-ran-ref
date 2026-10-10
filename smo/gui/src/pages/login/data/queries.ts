/** The sign-in page's one read: what the backend offers (`GET /api/auth/config`, PR-SEC-6 / PR-SEC-7.6). The page itself owns no other API
 * knowledge; the password and one-time-code steps go through `auth/AuthContext` (`POST /api/login`, `POST /api/login/totp`). */
import { useQuery } from "@tanstack/react-query";

import { api } from "../../../api/client";
import type { AuthConfig } from "../../../lib/oidc";

/** The sign-in options: OIDC when it is on, the password form unless it was switched off. Not retried: until it answers (or if it cannot) the
 * password form is shown, as before the redesign. */
export function useAuthConfig() {
  return useQuery<AuthConfig>({ queryKey: ["bff", "auth-config"], queryFn: () => api<AuthConfig>("/auth/config"), staleTime: 60_000, retry: false });
}
