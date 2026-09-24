import { createContext, useCallback, useContext, useEffect, useMemo, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { api, ApiError } from "../api/client";
import type { Me } from "../api/types";
import { can as canWith, type PermissionRule, type QueryValues, type Role } from "./rbac";

interface AuthState {
  me: Me | null;
  loading: boolean;
  role: Role | undefined;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  can: (method: string, path: string, query?: QueryValues) => boolean;
}

const AuthContext = createContext<AuthState | null>(null);

const isMeQuery = (key: readonly unknown[]) => key[0] === "bff" && key[1] === "me";

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const meQuery = useQuery<Me | null, ApiError>({
    queryKey: ["bff", "me"],
    queryFn: async () => {
      try {
        return await api<Me>("/me");
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null;
        throw e;
      }
    },
    staleTime: 60_000,
    retry: false,
  });
  const me = meQuery.data ?? null;

  const rulesQuery = useQuery<{ role: Role; rules: PermissionRule[] }, ApiError>({
    queryKey: ["bff", "permissions", me?.username],
    queryFn: () => api("/permissions"),
    enabled: me !== null,
    staleTime: 5 * 60_000,
  });

  // Any 401 from the BFF (expired or revoked session) drops back to login.
  useEffect(() => {
    const onUnauthorized = () => qc.setQueryData(["bff", "me"], null);
    window.addEventListener("smo:unauthorized", onUnauthorized);
    return () => window.removeEventListener("smo:unauthorized", onUnauthorized);
  }, [qc]);

  const login = useCallback(async (username: string, password: string) => {
    const result = await api<Me>("/login", { method: "POST", json: { username, password } });
    // Update the live "me" entry in place (qc.clear() would orphan the
    // observer above, leaving the app stuck on the login page), then drop
    // whatever the previous user had cached.
    qc.setQueryData(["bff", "me"], result);
    qc.removeQueries({ predicate: (q) => !isMeQuery(q.queryKey) });
  }, [qc]);

  const logout = useCallback(async () => {
    try { await api("/logout", { method: "POST" }); } finally {
      qc.setQueryData(["bff", "me"], null);
      qc.removeQueries({ predicate: (q) => !isMeQuery(q.queryKey) });
    }
  }, [qc]);

  const value = useMemo<AuthState>(() => {
    const rules = rulesQuery.data?.rules ?? [];
    return {
      me,
      loading: meQuery.isLoading,
      role: me?.role,
      login,
      logout,
      can: (method, path, query) => canWith(rules, me?.role, method, path, query),
    };
  }, [me, meQuery.isLoading, rulesQuery.data, login, logout]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
