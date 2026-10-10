/** The role matrix (`admin.roles`): what each GUI role may do. A fixed table that mirrors the BFF's permission rules (gui-bff/app/rbac.py `RULES`);
 * the BFF re-checks every call, so this card explains and never grants. */
import { Card } from "../../../components/ui";

/** The rows: what, and whether viewer / operator / admin may. */
const ROWS: [string, boolean, boolean, boolean][] = [
  ["Read status, lists, details, alarms, KPIs", true, true, true],
  ["Lifecycle: onboard, prime, deploy, upgrade, recover; train, advance, deploy models; ack/clear alarms; CM writes; policies, intents, orders; SA evaluate / remediate / escalate", false, true, true],
  ["Hard deletes & teardown: delete packages, terminate/delete instances, deprecate/delete models, delete policies & intents, terminate NF deployments, (de)provision O-Cloud resources", false, false, true],
  ["Test-data injection (alarms, rApp perf/faults, MLMF reports); GUI users and audit", false, false, true],
];

/** One cell: a tick with the word for screen readers, or a dash. */
function Mark({ yes }: { yes: boolean }) {
  return yes ? <span className="t-ok" aria-label="yes">✓</span> : <span className="faint" aria-label="no">—</span>;
}

/** The card. */
export function RoleMatrix() {
  return (
    <Card section="admin.roles" title="Role matrix" sub="the BFF re-checks every call">
      <div className="table-wrap" tabIndex={0}>
        <table className="table matrix">
          <thead><tr><th>Can</th><th>Viewer</th><th>Operator</th><th>Admin</th></tr></thead>
          <tbody>
            {ROWS.map(([what, v, o, a]) => <tr key={what}><td>{what}</td><td><Mark yes={v} /></td><td><Mark yes={o} /></td><td><Mark yes={a} /></td></tr>)}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
