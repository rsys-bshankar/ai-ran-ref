import { useState, type FormEvent } from "react";
import { NavLink, Outlet } from "react-router-dom";

import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { roleAtLeast, type Role } from "../auth/rbac";
import { Field, Modal } from "./ui";
import { useToast } from "./Toast";

export const NAV: { to: string; label: string; icon: string; minRole?: Role }[] = [
  { to: "/", label: "Dashboard", icon: "◧" },
  { to: "/rapps", label: "rApps", icon: "▣" },
  { to: "/aiml", label: "AI/ML", icon: "◈" },
  { to: "/alarms", label: "Alarms", icon: "⚠" },
  { to: "/kpis", label: "KPIs & Assurance", icon: "∿" },
  { to: "/policy", label: "Policy & Intents", icon: "⚖" },
  { to: "/infrastructure", label: "Infrastructure", icon: "▤" },
  { to: "/admin", label: "Admin", icon: "⚙", minRole: "admin" },
];

export function Layout() {
  const { me, logout } = useAuth();
  const [pwOpen, setPwOpen] = useState(false);
  if (!me) return null;
  return (
    <div className="shell">
      <nav className="sidebar" aria-label="Main">
        <div className="brand"><span className="brand-mark">M</span><div><strong>SMO</strong><span>Operator Console</span></div></div>
        <ul>
          {NAV.filter((n) => !n.minRole || roleAtLeast(me.role, n.minRole)).map((n) => (
            <li key={n.to}>
              <NavLink to={n.to} end={n.to === "/"} className={({ isActive }) => (isActive ? "nav active" : "nav")}>
                <span className="nav-icon" aria-hidden>{n.icon}</span>{n.label}
              </NavLink>
            </li>
          ))}
        </ul>
        <div className="sidebar-foot">
          <div className="whoami">
            <span className="avatar">{me.username.slice(0, 1).toUpperCase()}</span>
            <div><strong>{me.username}</strong><span className={`role role-${me.role}`}>{me.role}</span></div>
          </div>
          <div className="row gap">
            <button className="btn ghost small" onClick={() => setPwOpen(true)}>Password</button>
            <button className="btn ghost small" onClick={() => logout()}>Sign out</button>
          </div>
        </div>
      </nav>
      <main className="content"><Outlet /></main>
      {pwOpen && <ChangePassword onClose={() => setPwOpen(false)} />}
    </div>
  );
}

function ChangePassword({ onClose }: { onClose: () => void }) {
  const toast = useToast();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [error, setError] = useState<string | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    try {
      await api("/me/password", { method: "POST", json: { currentPassword: current, newPassword: next } });
      toast.push({ tone: "success", text: "Password changed" });
      onClose();
    } catch (err) {
      setError((err as Error).message);
    }
  };
  return (
    <Modal title="Change password" onClose={onClose}>
      <form onSubmit={submit} className="form">
        <Field label="Current password"><input type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required /></Field>
        <Field label="New password" hint="At least 8 characters"><input type="password" autoComplete="new-password" minLength={8} value={next} onChange={(e) => setNext(e.target.value)} required /></Field>
        {error && <div className="error-box">{error}</div>}
        <div className="row gap end"><button type="button" className="btn" onClick={onClose}>Cancel</button><button className="btn primary">Change</button></div>
      </form>
    </Modal>
  );
}
