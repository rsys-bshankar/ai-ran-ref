import { StrictMode, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";

import { ApiError } from "./api/client";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { roleAtLeast, type Role } from "./auth/rbac";
import { mustEnrol } from "./lib/mfa";
import { Layout } from "./shell/Layout";
import { ThemeProvider, usePreferences } from "./shell/ThemeProvider";
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
import { Preferences } from "./pages/preferences";
import { Topology } from "./pages/topology";
import { Configuration } from "./pages/configuration";
import { Software } from "./pages/software";
import { ElementDetail } from "./pages/element";
// Self-hosted fonts (the CSP allows fonts from 'self' only): IBM Plex Sans for text, Space Grotesk for headings and numbers, JetBrains Mono for ids.
import "@fontsource/ibm-plex-sans/latin-400.css";
import "@fontsource/ibm-plex-sans/latin-500.css";
import "@fontsource/ibm-plex-sans/latin-600.css";
import "@fontsource/space-grotesk/latin-500.css";
import "@fontsource/space-grotesk/latin-600.css";
import "@fontsource/space-grotesk/latin-700.css";
import "@fontsource/jetbrains-mono/latin-400.css";
import "@fontsource/jetbrains-mono/latin-600.css";
import "@fontsource/jetbrains-mono/latin-700.css";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // A 4xx won't fix itself on retry; transient 5xx/network errors might.
      retry: (count, err) => !(err instanceof ApiError && err.status < 500) && count < 2,
      refetchOnWindowFocus: true,
      staleTime: 2_000,
    },
  },
});

/** The index route: the Dashboard, or the start page the user chose in Preferences (only on a fresh visit to "/", never a redirect loop). */
function Home() {
  const { prefs } = usePreferences();
  const location = useLocation();
  const fresh = !(location.state as { stay?: boolean } | null)?.stay && location.key === "default";
  return fresh && prefs.startPage !== "/" ? <Navigate to={prefs.startPage} replace /> : <Dashboard />;
}

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
          <ThemeProvider>
          <BrowserRouter>
            <Routes>
              <Route path="/login" element={<Login />} />
              <Route element={<RequireAuth><Layout /></RequireAuth>}>
                <Route index element={<Home />} />
                <Route path="flows" element={<Flows />} />
                <Route path="flows/:flowId" element={<Flows />} />
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
                <Route path="topology" element={<Topology />} />
                <Route path="configuration" element={<Configuration />} />
                <Route path="software" element={<Software />} />
                <Route path="elements/:me" element={<ElementDetail />} />
                <Route path="preferences" element={<Preferences />} />
                <Route path="security" element={<Security />} />
                <Route path="admin" element={<RequireAuth minRole="admin"><Admin /></RequireAuth>} />
              </Route>
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </BrowserRouter>
          </ThemeProvider>
        </AuthProvider>
      </ToastProvider>
    </QueryClientProvider>
  </StrictMode>,
);
