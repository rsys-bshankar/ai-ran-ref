import { lazy, StrictMode, type ReactNode } from "react";
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
import { Dashboard } from "./pages/dashboard";
import { Login } from "./pages/login";
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

// Every page but the Dashboard and the sign-in is its own chunk, loaded when first opened: the shell and the first page paint from a small
// bundle (SCALE.md §0, "shell painted ≤ 300 ms"); the Dashboard and Login stay in it because one of them is always the first page.
const Admin = lazy(() => import("./pages/admin").then((m) => ({ default: m.Admin })));
const Aiml = lazy(() => import("./pages/aiml").then((m) => ({ default: m.Aiml })));
const Approvals = lazy(() => import("./pages/approvals").then((m) => ({ default: m.Approvals })));
const Alarms = lazy(() => import("./pages/alarms").then((m) => ({ default: m.Alarms })));
const Data = lazy(() => import("./pages/data").then((m) => ({ default: m.Data })));
const Flows = lazy(() => import("./pages/flows").then((m) => ({ default: m.Flows })));
const Infrastructure = lazy(() => import("./pages/infrastructure").then((m) => ({ default: m.Infrastructure })));
const Kpis = lazy(() => import("./pages/kpis").then((m) => ({ default: m.Kpis })));
const Policy = lazy(() => import("./pages/intents").then((m) => ({ default: m.Policy })));
const Rapps = lazy(() => import("./pages/rapps").then((m) => ({ default: m.Rapps })));
const RappDetail = lazy(() => import("./pages/rapp-detail").then((m) => ({ default: m.RappDetail })));
const Safeguards = lazy(() => import("./pages/safeguards").then((m) => ({ default: m.Safeguards })));
const Security = lazy(() => import("./pages/security").then((m) => ({ default: m.Security })));
const Preferences = lazy(() => import("./pages/preferences").then((m) => ({ default: m.Preferences })));
const Topology = lazy(() => import("./pages/topology").then((m) => ({ default: m.Topology })));
const Configuration = lazy(() => import("./pages/configuration").then((m) => ({ default: m.Configuration })));
const Software = lazy(() => import("./pages/software").then((m) => ({ default: m.Software })));
const ElementDetail = lazy(() => import("./pages/element").then((m) => ({ default: m.ElementDetail })));
const Decisions = lazy(() => import("./pages/decisions").then((m) => ({ default: m.Decisions })));
const Exports = lazy(() => import("./pages/exports").then((m) => ({ default: m.Exports })));
const DecisionDetail = lazy(() => import("./pages/decisions").then((m) => ({ default: m.DecisionDetail })));

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
                <Route path="exports" element={<RequireAuth minRole="operator"><Exports /></RequireAuth>} />
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
