/** The sidebar (248 px, BRIEF §2): the Radisys mark and product name, the environment chip (healthy/total SMO modules from `/api/modules/status`),
 * the grouped navigation with count badges (one summary call, `useSummary("nav")`), the user's pinned rApps under "rApps", and the user card
 * with role, one-time-code state, password change and sign-out. A user who must enrol a one-time code first sees only "Account security". */
import { NavLink } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";

import { api } from "../api/client";
import { POLL } from "../api/hooks";
import { usePins } from "../api/rapps";
import type { ModulesStatus } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { roleAtLeast } from "../auth/rbac";
import { KEYS } from "../data/keys";
import { sum, useSummary } from "../data/summary";
import { Icon } from "../kit/icons";
import { formatCount } from "../kit/Kpi";
import { NAV_GROUPS } from "./nav";

/** The rApps the user pinned (at most 5, kept by the GUI backend), listed under the one "rApps" entry. */
export function PinnedRapps() {
  const pins = usePins();
  const items = pins.data?.items ?? [];
  if (items.length === 0) return null;
  return (
    <ul className="pins" aria-label="Pinned rApps">
      {items.map((p) => (
        <li key={p.instanceId}>
          <NavLink to={`/rapps/${p.instanceId}`} className={({ isActive }) => (isActive ? "nav sub active" : "nav sub")} title={`${p.name ?? ""} ${p.version ?? ""} ${p.state ?? ""}`.trim()}>
            <span className="nav-icon" aria-hidden>↳</span>{p.name ?? `${p.instanceId.slice(0, 8)}…`}
          </NavLink>
        </li>
      ))}
    </ul>
  );
}

/** "21/21 modules" with a dot coloured by whether every module answered its health probe. */
function EnvChip() {
  const status = useQuery<ModulesStatus>({ queryKey: KEYS.modulesStatus, queryFn: () => api("/modules/status"), refetchInterval: POLL.status });
  const mods = status.data?.modules ?? [];
  const healthy = mods.filter((m) => m.healthy).length;
  const tone = !status.data ? "d-mute" : healthy === mods.length ? "d-ok" : healthy >= mods.length - 2 ? "d-warn" : "d-bad";
  return (
    <NavLink to="/#health" className="env-chip" title="SMO module health (GET /<module>/health via R1 Termination)">
      <span className={`dot ${tone}`} />
      <span className="grow">Phase 1 · SMO {status.data ? `${healthy}/${mods.length}` : "…"}</span>
      <span className="mono xs muted">modules</span>
    </NavLink>
  );
}

/** The sidebar. `onChangePassword` opens the password dialog the layout owns. */
export function Sidebar({ onChangePassword }: { onChangePassword: () => void }) {
  const { me, logout } = useAuth();
  const enrolOnly = !!me?.mfaEnrolmentRequired;
  const nav = useSummary("nav", { enabled: !!me && !enrolOnly, refetchInterval: 30_000 });
  if (!me) return null;
  return (
    <nav className="sidebar" aria-label="Main">
      <div className="brand">
        <img src="/brand/radisys-mark.png" alt="Radisys" width={36} height={36} />
        <div><strong>AI-RAN SMO</strong><span>Radisys · Operator Console</span></div>
      </div>
      {!enrolOnly && <EnvChip />}
      {NAV_GROUPS.map((g) => {
        const items = g.items.filter((n) => (!n.minRole || roleAtLeast(me.role, n.minRole)) && (!enrolOnly || n.to === "/security"));
        if (items.length === 0) return null;
        return (
          <div key={g.title} className="nav-group">
            <div className="eyebrow">{g.title}</div>
            <ul>
              {items.map((n) => {
                const n_ = n.badge ? sum(nav.data, n.badge.keys) : null;
                return (
                  <li key={n.to}>
                    <NavLink to={n.to} end={n.to === "/"} className={({ isActive }) => (isActive ? "nav active" : "nav")}>
                      <Icon name={n.icon} />
                      <span className="nav-label">{n.label}</span>
                      {n.badge && n_ !== null && n_ > 0 && <span className={`nav-count ${n.badge.tone}`} title={`${n_} ${n.badge.title}`}>{formatCount(n_, 999)}</span>}
                    </NavLink>
                    {n.to === "/rapps" && !enrolOnly && <PinnedRapps />}
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
      <div className="grow" />
      <div className="sidebar-foot">
        <div className="whoami">
          <span className="avatar" aria-hidden>{me.username.slice(0, 1).toUpperCase()}</span>
          <div>
            <strong>{me.username}</strong>
            <span className={`role role-${me.role}`}>{me.role}{me.local !== false && <> · MFA {me.totpEnrolled ? "on" : "off"}</>}</span>
          </div>
          <button type="button" className="btn ghost small icon" onClick={() => logout()} aria-label="Sign out" title="Sign out"><Icon name="signout" size={16} /></button>
        </div>
        {me.local !== false && <button type="button" className="btn ghost small" onClick={onChangePassword}>Change password</button>}
      </div>
    </nav>
  );
}
