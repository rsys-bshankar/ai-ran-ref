/** The role matrix (`admin.roles`): what each GUI role may do, drawn from the BFF's own permission table (GUI-10.4). Each row names a few calls
 * that stand for it (`ROLE_ROWS`); a role gets a tick when the live table (`GET /api/permissions`, gui-bff/app/rbac.py `RULES`) allows it every
 * one of them, so the card cannot drift from the rules. The BFF's own routes (users, audit log, export jobs) are not in that table; their rows
 * carry the role the BFF checks in code (`bff`). The BFF re-checks every call, so this card explains and never grants. */
import { useAuth } from "../../../auth/AuthContext";
import { can, ROLES, type PermissionRule, type Role } from "../../../auth/rbac";
import { Card } from "../../../components/ui";

/** One row: what it means, and either the calls that stand for it (checked against the table) or the minimum role of a BFF-own route. */
export interface RoleRow { what: string; probes?: [string, string][]; bff?: Role }

/** The rows, in order of the role they need. */
export const ROLE_ROWS: RoleRow[] = [
  { what: "Read status, lists, details, alarms, KPIs", probes: [["GET", "/rapp-mgmt/instances"], ["GET", "/ran-nf-oam/alarms"], ["GET", "/mlmr/models"]] },
  { what: "Lifecycle: onboard, prime, deploy rApps; train models; ack/clear alarms; CM writes; PM and FM subscriptions; intents, orders; approve rApp actions; stop all rApp writes",
    probes: [["POST", "/onboarding/packages/x/prime"], ["POST", "/rapp-mgmt/instances"], ["POST", "/aimgf/training-jobs"], ["PATCH", "/ran-nf-oam/alarms/x/ack"],
      ["POST", "/ran-nf-oam/config-jobs"], ["POST", "/ran-nf-oam/pm-subscriptions"], ["POST", "/ran-nf-oam/fm-subscriptions"], ["DELETE", "/ran-nf-oam/fm-subscriptions/x"],
      ["POST", "/intent-service/intents"], ["POST", "/so-smos/orders"], ["POST", "/ran-nf-oam/rapp-approvals/x/approve"], ["PUT", "/rapp-mgmt/kill-all"]] },
  { what: "Read feature groups (they carry datalake tokens)", probes: [["GET", "/aimgf/feature-groups"]] },
  { what: "Export decision records", bff: "operator" },
  { what: "Hard deletes and teardown: delete packages, terminate/delete instances, delete models and intents, terminate NF deployments, (de)provision O-Cloud resources",
    probes: [["DELETE", "/onboarding/packages/x"], ["POST", "/rapp-mgmt/instances/x/terminate"], ["DELETE", "/rapp-mgmt/instances/x"], ["DELETE", "/mlmr/models/x"],
      ["DELETE", "/intent-service/intents/x"], ["DELETE", "/nfo/deployments/x"], ["POST", "/focom/resources/provision"], ["DELETE", "/focom/resources/x"]] },
  { what: "Registry and access administration: SME providers and invokers, MSAC, rApp limits, resume all rApp writes",
    probes: [["POST", "/sme/provider-registrations"], ["POST", "/sme/invoker-registrations"], ["POST", "/ran-nf-oam/msac/roles"], ["PUT", "/ran-nf-oam/rapp-limits/x"], ["DELETE", "/rapp-mgmt/kill-all"]] },
  { what: "Test-data injection (alarms, rApp performance)", probes: [["POST", "/ran-nf-oam/alarms/ingest"], ["POST", "/focom/alarms/ingest"], ["POST", "/rapp-mgmt/instances/x/performance"]] },
  { what: "GUI users, the audit log and its export", bff: "admin" },
];

/** Whether `role` may do what `row` stands for under `rules`: every probe allowed (a probe no rule matches is refused), or the BFF-own role. */
export function rowAllows(rules: PermissionRule[], row: RoleRow, role: Role): boolean {
  if (row.bff) return ROLES.indexOf(role) >= ROLES.indexOf(row.bff);
  return (row.probes ?? []).every(([method, path]) => can(rules, role, method, path));
}

/** One cell: a tick with the word for screen readers, or a dash. */
function Mark({ yes }: { yes: boolean }) {
  return yes ? <span className="t-ok" aria-label="yes">✓</span> : <span className="faint" aria-label="no">—</span>;
}

/** The card; while the table is not read yet it says so instead of showing an all-dash matrix. */
export function RoleMatrix() {
  const { rules } = useAuth();
  return (
    <Card section="admin.roles" title="Role matrix" sub="from the BFF's permission table · it re-checks every call">
      {rules.length === 0 ? <p className="small muted">Reading the permission table…</p> : (
        <div className="table-wrap" tabIndex={0}>
          <table className="table matrix">
            <thead><tr><th>Can</th><th>Viewer</th><th>Operator</th><th>Admin</th></tr></thead>
            <tbody>
              {ROLE_ROWS.map((row) => (
                <tr key={row.what}><td>{row.what}{row.bff && <span className="xs muted"> (BFF route)</span>}</td>
                  {ROLES.map((r) => <td key={r}><Mark yes={rowAllows(rules, row, r)} /></td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
