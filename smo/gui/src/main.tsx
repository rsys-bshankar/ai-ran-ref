/**
 * The entry point of the single-page app: creates the query client, mounts the providers (react-query, toasts, the session, the router) and declares every route. `/login` is the only route outside `RequireAuth`; all the others render inside `Layout`
 * and need a signed-in user, and `/admin` additionally needs the admin role. An unknown path goes to the dashboard. The Onboarding and Campaigns pages are not routes: they are tabs of the Infrastructure page, and the rApp directory is a tab of the rApps page.
 * The route guards decide what to show; the BFF checks every call again.
 */

import { StrictMode, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";

import { ApiError } from "./api/client";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { roleAtLeast, type Role } from "./auth/rbac";
import { mustEnrol } from "./lib/mfa";
import { Layout } from "./components/Layout";
import { ToastProvider } from "./components/Toast";
import { Admin } from "./pages/Admin";
import { Aiml } from "./pages/Aiml";
import { Approvals } from "./pages/Approvals";
import { Alarms } from "./pages/Alarms";
import { Dashboard } from "./pages/Dashboard";
import { DecisionDetail, Decisions } from "./pages/Decisions";
import { Data } from "./pages/Data";
import { Flows } from "./pages/Flows";
import { Infrastructure } from "./pages/Infrastructure";
import { Kpis } from "./pages/Kpis";
import { Login } from "./pages/Login";
import { Policy } from "./pages/Policy";
import { Rapps } from "./pages/Rapps";
import { RappDetail } from "./pages/RappDetail";
import { Safeguards } from "./pages/Safeguards";
import { Security } from "./pages/Security";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // A 4xx won't fix itself on retry; transient 5xx/network errors might (at most two retries).
      retry: (count, err) => !(err instanceof ApiError && err.status < 500) && count < 2,
      refetchOnWindowFocus: true,
      staleTime: 2_000,
    },
  },
});

/**
 * Route guard. Waits for the session to load, sends a signed-out visitor to /login (remembering where they were going), sends a local admin who must still enrol a one-time code to /security (PR-SEC-7.8; the backend refuses every other route until then),
 * and, when `minRole` is given, sends a user below that role to the dashboard. Otherwise it draws its children.
 */
function RequireAuth({ children, minRole }: { children: ReactNode; minRole?: Role }) {
  const { me, loading } = useAuth();
  const location = useLocation();
  if (loading) return <div className="boot muted">Loading…</div>;
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  // PR-SEC-7.8: a local admin who must enrol a one-time code sees nothing else until they have (the backend refuses every other route)
  if (mustEnrol(me, location.pathname)) return <Navigate to="/security" replace />;
  if (minRole && !roleAtLeast(me.role, minRole)) return <Navigate to="/" replace />;
  return <>{children}</>;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ToastProvider>
        <AuthProvider>
          <BrowserRouter>
            <Routes>
              <Route path="/login" element={<Login />} />
              <Route element={<RequireAuth><Layout /></RequireAuth>}>
                <Route index element={<Dashboard />} />
                <Route path="flows" element={<Flows />} />
                <Route path="rapps" element={<Rapps />} />
                <Route path="rapps/:instanceId" element={<RappDetail />} />
                <Route path="safeguards" element={<Safeguards />} />
                <Route path="approvals" element={<Approvals />} />
                <Route path="decisions" element={<Decisions />} />
                <Route path="decisions/:decisionId" element={<DecisionDetail />} />
                <Route path="aiml" element={<Aiml />} />
                <Route path="alarms" element={<Alarms />} />
                <Route path="kpis" element={<Kpis />} />
                <Route path="policy" element={<Policy />} />
                <Route path="infrastructure" element={<Infrastructure />} />
                <Route path="data" element={<Data />} />
                <Route path="security" element={<Security />} />
                <Route path="admin" element={<RequireAuth minRole="admin"><Admin /></RequireAuth>} />
              </Route>
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </BrowserRouter>
        </AuthProvider>
      </ToastProvider>
    </QueryClientProvider>
  </StrictMode>,
);
