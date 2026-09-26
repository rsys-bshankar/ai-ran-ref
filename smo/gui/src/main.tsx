import { StrictMode, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";

import { ApiError } from "./api/client";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { roleAtLeast, type Role } from "./auth/rbac";
import { Layout } from "./components/Layout";
import { ToastProvider } from "./components/Toast";
import { Admin } from "./pages/Admin";
import { Aiml } from "./pages/Aiml";
import { Alarms } from "./pages/Alarms";
import { Dashboard } from "./pages/Dashboard";
import { Data } from "./pages/Data";
import { Flows } from "./pages/Flows";
import { Infrastructure } from "./pages/Infrastructure";
import { Kpis } from "./pages/Kpis";
import { Login } from "./pages/Login";
import { Policy } from "./pages/Policy";
import { Rapps } from "./pages/Rapps";
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

function RequireAuth({ children, minRole }: { children: ReactNode; minRole?: Role }) {
  const { me, loading } = useAuth();
  const location = useLocation();
  if (loading) return <div className="boot muted">Loading…</div>;
  if (!me) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
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
                <Route path="aiml" element={<Aiml />} />
                <Route path="alarms" element={<Alarms />} />
                <Route path="kpis" element={<Kpis />} />
                <Route path="policy" element={<Policy />} />
                <Route path="infrastructure" element={<Infrastructure />} />
                <Route path="data" element={<Data />} />
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
