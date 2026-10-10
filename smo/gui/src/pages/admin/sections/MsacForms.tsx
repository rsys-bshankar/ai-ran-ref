/** The create forms of the MSAC tab (`admin.msac`): a new role (name and the access rules it grants), a new identity (type, name, optional
 * credential, roles) and a new access rule (name, data-node selector, operations, ALLOW or DENY), each a `POST /ran-nf-oam/msac/...` the BFF allows
 * to an admin only (GUI-9.7). Each form is wrapped in `Can`, so a lower role sees none; the role and rule pickers read the first 100 of each. */
import { useState } from "react";

import { useSmo } from "../../../api/hooks";
import { ActionButton, Can, Field } from "../../../components/ui";
import { splitList } from "../../../lib/domain";
import { MSAC } from "../data/queries";
import type { IdentityType, MsacAccessRule, MsacRole } from "../data/types";

/** The identity types, in the order MSAC lists them. */
export const IDENTITY_TYPES: IdentityType[] = ["USERNAME", "EMAIL_ADDRESS", "PHONE_NUMBER", "IP_ADDRESS", "MACHINEUSER"];
/** The operations an access rule may allow or deny. */
export const OPERATIONS = ["create", "read", "update", "delete", "exec"] as const;

/** The values chosen in a multiple `<select>`. */
const chosen = (e: { target: HTMLSelectElement }) => Array.from(e.target.selectedOptions).map((o) => o.value);

/** "New role": its name and the access rules it grants. */
export function NewRole() {
  const rules = useSmo<MsacAccessRule[]>(MSAC.rules, { limit: 100 });
  const [name, setName] = useState("");
  const [ruleIds, setRuleIds] = useState<string[]>([]);
  return (
    <Can method="POST" path={MSAC.roles}>
      <details className="inset" data-part="new-role"><summary className="small">New role</summary>
        <div className="form">
          <Field label="Role name"><input value={name} onChange={(e) => setName(e.target.value)} aria-label="Role name" /></Field>
          <Field label="Access rules" hint="Ctrl/⌘-click for several"><select multiple value={ruleIds} onChange={(e) => setRuleIds(chosen(e))} aria-label="Access rules">
            {rules.data?.map((r) => <option key={r.id} value={r.id}>{r.attributes.ruleName} · {r.attributes.actions}</option>)}</select></Field>
          <div className="row end"><ActionButton label="Create role" tone="primary" disabled={!name.trim()}
            action={{ method: "POST", path: MSAC.roles, json: { roleName: name.trim(), accessRulesList: ruleIds }, success: `Role ${name.trim()} created` }}
            onDone={() => { setName(""); setRuleIds([]); }} /></div>
        </div>
      </details>
    </Can>
  );
}

/** "New identity": who, by type and name, with an optional credential and the roles it holds. */
export function NewIdentity() {
  const roles = useSmo<MsacRole[]>(MSAC.roles, { limit: 100 });
  const [type, setType] = useState<IdentityType>("USERNAME");
  const [name, setName] = useState("");
  const [credential, setCredential] = useState("");
  const [roleIds, setRoleIds] = useState<string[]>([]);
  return (
    <Can method="POST" path={MSAC.identities}>
      <details className="inset" data-part="new-identity"><summary className="small">New identity</summary>
        <div className="form">
          <Field label="Type"><select value={type} onChange={(e) => setType(e.target.value as IdentityType)} aria-label="Identity type">{IDENTITY_TYPES.map((t) => <option key={t}>{t}</option>)}</select></Field>
          <Field label="Name"><input value={name} onChange={(e) => setName(e.target.value)} aria-label="Identity name" /></Field>
          <Field label="Credential" hint="Optional; never shown again"><input type="password" autoComplete="new-password" value={credential} onChange={(e) => setCredential(e.target.value)} aria-label="Credential" /></Field>
          <Field label="Roles" hint="Ctrl/⌘-click for several"><select multiple value={roleIds} onChange={(e) => setRoleIds(chosen(e))} aria-label="Roles">
            {roles.data?.map((r) => <option key={r.id} value={r.id}>{r.attributes.roleName}</option>)}</select></Field>
          <div className="row end"><ActionButton label="Create identity" tone="primary" disabled={!name.trim()}
            action={{ method: "POST", path: MSAC.identities, json: { identityType: type, identityName: name.trim(), credential: credential || null, roleList: roleIds }, success: `Identity ${name.trim()} created` }}
            onDone={() => { setName(""); setCredential(""); setRoleIds([]); }} /></div>
        </div>
      </details>
    </Can>
  );
}

/** "New access rule": ALLOW or DENY of some operations on the data nodes a selector picks. */
export function NewRule() {
  const [name, setName] = useState("");
  const [selector, setSelector] = useState("");
  const [ops, setOps] = useState<string[]>(["read"]);
  const [actions, setActions] = useState<"ALLOW" | "DENY">("ALLOW");
  const [components, setComponents] = useState("");
  const toggle = (o: string) => setOps((x) => (x.includes(o) ? x.filter((y) => y !== o) : [...x, o]));
  return (
    <Can method="POST" path={MSAC.rules}>
      <details className="inset" data-part="new-rule"><summary className="small">New access rule</summary>
        <div className="form">
          <Field label="Rule name"><input value={name} onChange={(e) => setName(e.target.value)} aria-label="Rule name" /></Field>
          <Field label="Data-node selector" hint="Which part of the RAN data model, e.g. /ManagedElement=*/GNBDUFunction=*"><input className="mono" value={selector} onChange={(e) => setSelector(e.target.value)} aria-label="Data-node selector" /></Field>
          <Field label="Operations"><span className="row wrap">{OPERATIONS.map((o) => (
            <label key={o} className="check"><input type="checkbox" checked={ops.includes(o)} onChange={() => toggle(o)} /> {o}</label>))}</span></Field>
          <Field label="Action"><select value={actions} onChange={(e) => setActions(e.target.value as "ALLOW" | "DENY")} aria-label="Action"><option>ALLOW</option><option>DENY</option></select></Field>
          <Field label="Component data" hint="Optional, comma-separated"><input value={components} onChange={(e) => setComponents(e.target.value)} aria-label="Component data" /></Field>
          <div className="row end"><ActionButton label="Create rule" tone="primary" disabled={!name.trim() || !selector.trim() || ops.length === 0}
            action={{ method: "POST", path: MSAC.rules, json: { ruleName: name.trim(), dataNodeSelector: selector.trim(), operations: ops, actions, componentCData: splitList(components) },
              success: `Access rule ${name.trim()} created` }}
            onDone={() => { setName(""); setSelector(""); setComponents(""); }} /></div>
        </div>
      </details>
    </Can>
  );
}
