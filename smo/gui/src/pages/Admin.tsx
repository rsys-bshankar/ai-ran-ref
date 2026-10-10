/**
 * The Admin page (route /admin): the GUI users and their roles, which the BFF holds and the SMO does not, and the audit log of every mutating call. Admin only: main.tsx wraps the route in
 * `RequireAuth minRole="admin"`, the menu hides the entry from other roles, and the BFF's /api/admin/* routes require the admin role themselves (`require_admin`), so the checks here are for
 * the display. It calls the BFF directly (`api`), not the SMO proxy, and its caches are keyed ["bff", "admin", ...] so a change refreshes the lists.
 * The role matrix at the bottom is a fixed text, not read from the permission table: update it together with gui-bff/app/rbac.py.
 */

import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { unwrapPage } from "../api/hooks";
import { api, ApiError, type RequestOptions } from "../api/client";
import type { AuditEntry, GuiUser } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { ROLES, type Role } from "../auth/rbac";
import { useToast } from "../components/Toast";
import { Card, DataTable, Field, Modal, PageHeader, StateBadge, Tabs, useHashTab } from "../components/ui";
import { formatTime } from "../lib/domain";
import { mfaLabel } from "../lib/mfa";

const TABS = ["users", "audit"] as const;

/**
 * Returns a mutation for one BFF admin call (`path`, request options and the success text): toasts the success text or the error message and refreshes the admin lists.
 */
function useBffMutation() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation<unknown, ApiError, { path: string; opts: RequestOptions; success: string }>({
    mutationFn: ({ path, opts }) => api(path, opts),
    onSuccess: (_d, v) => { toast.push({ tone: "success", text: v.success }); qc.invalidateQueries({ queryKey: ["bff", "admin"] }); },
    onError: (e) => toast.push({ tone: "error", text: e.message }),
  });
}

/** The page: header, the Users and Audit tabs (kept in the URL hash) and the role matrix. */
export function Admin() {
  const [tab, setTab] = useHashTab(TABS, "users");
  return (
    <>
      <PageHeader title="Admin" subtitle="GUI users and roles (held by the BFF, not the SMO), and the audit log of every mutating call" />
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "users", label: "Users & roles" }, { id: "audit", label: "Audit log" }]} />
      {tab === "users" ? <Users /> : <Audit />}
      <Card title="Role matrix">
        <table className="table matrix">
          <thead><tr><th /><th>Viewer</th><th>Operator</th><th>Admin</th></tr></thead>
          <tbody>
            <tr><td>Read status, lists, details, alarms, KPIs</td><td>✓</td><td>✓</td><td>✓</td></tr>
            <tr><td>Lifecycle: onboard, prime, deploy, upgrade, recover; train, advance, deploy models; ack/clear alarms; CM writes; policies, intents, orders; SA evaluate / remediate / escalate</td><td /><td>✓</td><td>✓</td></tr>
            <tr><td>Hard deletes &amp; teardown: delete packages, terminate/delete instances, deprecate/delete models, delete policies &amp; intents, terminate NF deployments, (de)provision O-Cloud resources</td><td /><td /><td>✓</td></tr>
            <tr><td>Test-data injection (alarms, rApp perf/faults, MLMF reports); GUI users and audit</td><td /><td /><td>✓</td></tr>
          </tbody>
        </table>
      </Card>
    </>
  );
}

/**
 * The users table with its row actions: change role, activate or deactivate, break-glass flag (not for identity-provider users), reset password, end every session, remove the one-time code
 * (asks first) and delete (not offered for yourself). Each action is a PATCH, POST or DELETE on /api/admin/users/<name>. Destructive actions ask with `window.confirm`; the BFF is the authority on what is allowed.
 */
function Users() {
  const { me } = useAuth();
  const users = useQuery<GuiUser[], ApiError>({ queryKey: ["bff", "admin", "users"], queryFn: () => api("/admin/users") });
  const m = useBffMutation();
  const [creating, setCreating] = useState(false);
  const [resetting, setResetting] = useState<string | null>(null);
  const patch = (username: string, json: Record<string, unknown>, success: string) => m.mutate({ path: `/admin/users/${username}`, opts: { method: "PATCH", json }, success });
  return (
    <Card title="Users" actions={<button className="btn primary" onClick={() => setCreating(true)}>Add user</button>}>
      <DataTable rows={users.data} loading={users.isLoading} error={users.error} rowKey={(u) => u.username} columns={[
        { header: "User", render: (u) => <><strong>{u.username}</strong>{u.username === me?.username && <span className="muted small"> (you)</span>}</> },
        { header: "Role", render: (u) => (
          <select value={u.role} aria-label={`Role for ${u.username}`} onChange={(e) => patch(u.username, { role: e.target.value }, `${u.username} is now ${e.target.value}`)}>
            {ROLES.map((r) => <option key={r}>{r}</option>)}
          </select>
        ) },
        { header: "Status", render: (u) => <StateBadge state={u.active ? "ACTIVE" : "DISABLED"} /> },
        { header: "One-time code", render: (u) => mfaLabel(u) },
        { header: "Break-glass", render: (u) => (
          <input type="checkbox" checked={Boolean(u.breakGlass)} disabled={u.username.startsWith("oidc:")} aria-label={`Break-glass for ${u.username}`}
                 onChange={(e) => patch(u.username, { breakGlass: e.target.checked }, `${u.username} ${e.target.checked ? "is now" : "is no longer"} a break-glass account`)} />
        ) },
        { header: "Created", render: (u) => formatTime(u.createdAt) },
        { header: "", className: "actions", render: (u) => (
          <div className="row gap end">
            <button className="btn" onClick={() => setResetting(u.username)}>Reset password</button>
            <button className="btn" onClick={() => window.confirm(`End every session of ${u.username}?`) && m.mutate({ path: `/admin/users/${u.username}/revoke-sessions`, opts: { method: "POST" }, success: `Sessions of ${u.username} ended` })}>Revoke sessions</button>
            {u.totpEnrolled && <button className="btn" onClick={() => window.confirm(`Remove the one-time code and recovery codes of ${u.username}? They sign in with the password alone until they enrol a new one.`) && m.mutate({ path: `/admin/users/${u.username}/reset-totp`, opts: { method: "POST" }, success: `One-time code of ${u.username} removed` })}>Reset one-time code</button>}
            <button className="btn" onClick={() => patch(u.username, { active: !u.active }, `${u.username} ${u.active ? "deactivated" : "activated"}`)}>{u.active ? "Deactivate" : "Activate"}</button>
            {u.username !== me?.username && <button className="btn danger" onClick={() => window.confirm(`Delete user ${u.username}?`) && m.mutate({ path: `/admin/users/${u.username}`, opts: { method: "DELETE" }, success: `${u.username} deleted` })}>Delete</button>}
          </div>
        ) },
      ]} />
      <p className="muted small">Role changes apply on the user's next request; password resets, deactivation and &quot;Revoke sessions&quot; end their existing sessions. A break-glass account can sign in with its password and one-time code even when the console accepts only the identity provider; it needs a one-time code to sign in at all.</p>
      {creating && <CreateUser onClose={() => setCreating(false)} />}
      {resetting && <ResetPassword username={resetting} onClose={() => setResetting(null)} />}
    </Card>
  );
}

/**
 * The dialog that creates a GUI user (username, initial password, role). The username is lower-cased as typed and checked against the BFF's pattern; the password needs at least 8 characters.
 */
function CreateUser({ onClose }: { onClose: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const m = useBffMutation();
  const submit = (e: FormEvent) => {
    e.preventDefault();
    m.mutate({ path: "/admin/users", opts: { method: "POST", json: { username, password, role } }, success: `User ${username} created` }, { onSuccess: onClose });
  };
  return (
    <Modal title="Add user" onClose={onClose}>
      <form className="form" onSubmit={submit}>
        <Field label="Username" hint="lowercase letters, digits, _ . -"><input value={username} onChange={(e) => setUsername(e.target.value.toLowerCase())} required pattern="[a-z][a-z0-9_.\-]{1,31}" /></Field>
        <Field label="Initial password" hint="At least 8 characters"><input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} minLength={8} required /></Field>
        <Field label="Role"><select value={role} onChange={(e) => setRole(e.target.value as Role)}>{ROLES.map((r) => <option key={r}>{r}</option>)}</select></Field>
        <div className="row gap end"><button type="button" className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={m.isPending}>Create</button></div>
      </form>
    </Modal>
  );
}

/** The dialog that sets a new password for a user; the BFF ends that user's existing sessions. */
function ResetPassword({ username, onClose }: { username: string; onClose: () => void }) {
  const [password, setPassword] = useState("");
  const m = useBffMutation();
  return (
    <Modal title={`Reset password — ${username}`} onClose={onClose}>
      <form className="form" onSubmit={(e) => { e.preventDefault(); m.mutate({ path: `/admin/users/${username}`, opts: { method: "PATCH", json: { password } }, success: `Password reset for ${username}` }, { onSuccess: onClose }); }}>
        <Field label="New password" hint="Ends the user's existing sessions"><input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} minLength={8} required /></Field>
        <div className="row gap end"><button type="button" className="btn" onClick={onClose}>Cancel</button><button className="btn primary" disabled={m.isPending}>Reset</button></div>
      </form>
    </Modal>
  );
}

/**
 * The audit log tab: the latest 300 entries (polled every 10 s), filtered by user name and by event. The outcome badge is derived from the event (DENIED, failed or locked sign-ins read as rejected, a proxied call with
 * status 400 or more as failed). The list of events is fixed text; an event the BFF adds is shown in the table but cannot be filtered until it is added here.
 */
function Audit() {
  const [username, setUsername] = useState("");
  const [action, setAction] = useState("");
  const entries = useQuery<AuditEntry[], ApiError>({
    queryKey: ["bff", "admin", "audit", username, action],
    queryFn: async () => unwrapPage<AuditEntry[]>(await api<unknown>("/admin/audit", { query: { limit: 300, username, action } })),
    refetchInterval: 10_000,
  });
  return (
    <Card title="Audit log" actions={<>
      <input placeholder="User" value={username} onChange={(e) => setUsername(e.target.value)} aria-label="Filter by user" />
      <select value={action} onChange={(e) => setAction(e.target.value)} aria-label="Filter by action">
        <option value="">All actions</option>
        {["PROXY", "DENIED", "LOGIN", "LOGIN_FAILED", "LOGIN_LOCKED", "LOGIN_REFUSED", "MFA_CHALLENGE", "BREAK_GLASS_LOGIN", "RECOVERY_CODE_USED", "LOGOUT", "TOKEN", "PASSWORD_CHANGED", "TOTP_ENROL_STARTED", "TOTP_ENROLLED", "TOTP_RESET", "RECOVERY_CODES_REGENERATED", "USER_CREATED", "USER_UPDATED", "USER_DELETED", "USER_SESSIONS_REVOKED"].map((a) => <option key={a}>{a}</option>)}
      </select>
    </>}>
      <p className="muted small">Append-only. Every mutating call the BFF proxies (allowed or denied) plus sign-ins and user administration.</p>
      <DataTable rows={entries.data} loading={entries.isLoading} error={entries.error} rowKey={(e) => String(e.id)} empty="No entries." columns={[
        { header: "When", render: (e) => formatTime(e.at) },
        { header: "User", render: (e) => <>{e.username ?? "—"}{e.role && <span className="muted small"> ({e.role})</span>}</> },
        { header: "Outcome", render: (e) => <StateBadge state={e.action === "DENIED" || ["LOGIN_FAILED", "LOGIN_LOCKED", "LOGIN_REFUSED"].includes(e.action) ? "REJECTED" : e.action === "PROXY" && (e.statusCode ?? 0) >= 400 ? "FAILED" : "COMPLETED"} /> },
        { header: "Event", render: (e) => <code className="small">{e.action}</code> },
        { header: "Call", render: (e) => e.method ? <code className="small">{e.method} {e.path}</code> : <span className="muted">—</span> },
        { header: "Status", render: (e) => e.statusCode ?? "—" },
        { header: "Detail", render: (e) => <span className="small">{e.detail ?? ""}</span> },
      ]} />
    </Card>
  );
}
