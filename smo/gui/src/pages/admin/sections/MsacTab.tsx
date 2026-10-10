/** The "RAN access control (MSAC)" tab of Admin (`admin.msac`, BRIEF §4e feature 5): RAN NF OAM's roles, identities (USERNAME, EMAIL_ADDRESS,
 * PHONE_NUMBER, IP_ADDRESS, MACHINEUSER) and access rules (ALLOW or DENY of operations on a data-node selector), each a server-paged table. MSAC
 * decides what a config write may touch; the GUI roles decide what a person may click. Delete buttons are role-gated by `ActionButton`: the BFF's
 * permission table has no MSAC write rule today, so they render for nobody until one is added (gui-bff/app/rbac.py). */
import { ActionButton, Card, Id } from "../../../components/ui";
import { Badge } from "../../../kit/Badge";
import { Callout } from "../../../kit/Callout";
import { ServerTable } from "../../../kit/ServerTable";
import { MSAC } from "../data/queries";
import type { MsacAccessRule, MsacIdentity, MsacRole } from "../data/types";

/** A delete button for one MSAC object (shown only when the BFF allows the call). */
function Delete({ path, id, name }: { path: string; id: string; name: string }) {
  return <ActionButton tone="danger" label="Delete" confirm={`Delete ${name}?`} action={{ method: "DELETE", path: `${path}/${id}`, success: `${name} deleted` }} />;
}

/** The tab: an explanation and the three tables. */
export function MsacTab() {
  return (
    <div className="stack" data-section="admin.msac">
      <Callout tone="volt" title="RAN access control (MSAC)">
        Decides which identity may read or change which part of the RAN data model. Every config job is checked against it before anything is sent.
        GUI roles decide what a person may click; MSAC decides what a write may touch.
      </Callout>
      <div className="grid g2">
        <Card section="admin.msac.roles" title="Roles" sub="named sets of access rules">
          <ServerTable<MsacRole> path={MSAC.roles} rowKey={(r) => r.id} empty="No MSAC roles." columns={[
            { header: "Role", render: (r) => <strong>{r.attributes.roleName}</strong> },
            { header: "Access rules", render: (r) => <span className="num">{r.attributes.accessRulesList.length}</span> },
            { header: "Id", render: (r) => <Id value={r.id} /> },
            { header: "", className: "actions", render: (r) => <Delete path={MSAC.roles} id={r.id} name={`role ${r.attributes.roleName}`} /> },
          ]} />
        </Card>
        <Card section="admin.msac.identities" title="Identities" sub="who holds which roles">
          <ServerTable<MsacIdentity> path={MSAC.identities} rowKey={(i) => i.id} empty="No MSAC identities." columns={[
            { header: "Identity", render: (i) => <strong className="mono small">{i.attributes.identityName}</strong> },
            { header: "Type", render: (i) => <Badge tone="info" plain>{i.attributes.identityType}</Badge> },
            { header: "Roles", render: (i) => <span className="num">{i.attributes.roleList.length}</span> },
            { header: "", className: "actions", render: (i) => <Delete path={MSAC.identities} id={i.id} name={`identity ${i.attributes.identityName}`} /> },
          ]} />
        </Card>
      </div>
      <Card section="admin.msac.rules" title="Access rules" sub="ALLOW or DENY of operations on the data nodes a selector picks">
        <ServerTable<MsacAccessRule> path={MSAC.rules} rowKey={(r) => r.id} empty="No MSAC access rules." columns={[
          { header: "Rule", render: (r) => <strong>{r.attributes.ruleName}</strong> },
          { header: "Action", render: (r) => <Badge tone={r.attributes.actions === "ALLOW" ? "ok" : "bad"}>{r.attributes.actions}</Badge> },
          { header: "Operations", render: (r) => <span className="row wrap">{r.attributes.operations.map((o) => <span key={o} className="chip">{o}</span>)}</span> },
          { header: "Data nodes", render: (r) => <code className="small">{r.attributes.dataNodeSelector}</code> },
          { header: "", className: "actions", render: (r) => <Delete path={MSAC.rules} id={r.id} name={`rule ${r.attributes.ruleName}`} /> },
        ]} />
      </Card>
    </div>
  );
}
