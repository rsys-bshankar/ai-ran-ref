/** The Users tab's dialogs (BRIEF §4b "Admin: the Add user dialog"): add a local user, and reset a user's password. Both post to the BFF
 * (`POST /api/admin/users`, `PATCH /api/admin/users/{username}`); a reset ends the user's existing sessions. */
import { useState, type FormEvent } from "react";

import { ROLES, type Role } from "../../../auth/rbac";
import { Field, Modal } from "../../../components/ui";
import { useBffMutation } from "../data/queries";

/** Add a local user with an initial password and a role. Closes on success. */
export function CreateUser({ onClose }: { onClose: () => void }) {
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
        <p className="muted small">Users of the identity provider are not added here: they get a row on their first sign-in.</p>
        <div className="row gap end"><button type="button" className="btn" onClick={onClose}>Cancel</button><button type="submit" className="btn primary" disabled={m.isPending}>Create</button></div>
      </form>
    </Modal>
  );
}

/** Set a new password for `username`; ends their existing sessions. Closes on success. */
export function ResetPassword({ username, onClose }: { username: string; onClose: () => void }) {
  const [password, setPassword] = useState("");
  const m = useBffMutation();
  const submit = (e: FormEvent) => {
    e.preventDefault();
    m.mutate({ path: `/admin/users/${username}`, opts: { method: "PATCH", json: { password } }, success: `Password reset for ${username}` }, { onSuccess: onClose });
  };
  return (
    <Modal title={`Reset password — ${username}`} onClose={onClose}>
      <form className="form" onSubmit={submit}>
        <Field label="New password" hint="Ends the user's existing sessions"><input type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} minLength={8} required /></Field>
        <div className="row gap end"><button type="button" className="btn" onClick={onClose}>Cancel</button><button type="submit" className="btn primary" disabled={m.isPending}>Reset</button></div>
      </form>
    </Modal>
  );
}
