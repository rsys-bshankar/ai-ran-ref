/** Recent sign-ins (`security.signins`, handoff `Security.dc.html`). ⚠ Data gap (BRIEF §5 "sign-in history"): the BFF records sign-ins in its audit
 * log, but only an admin may read that (`GET /api/admin/audit`) and there is no per-user "my sign-ins" route, so the box says so instead of a table. */
import { Card } from "../../../components/ui";

/** The box, with its gap note. */
export function SignIns() {
  return (
    <Card section="security.signins" title="Recent sign-ins" sub="when, how and from where you signed in">
      <p className="kpi-v" aria-label="not available">—</p>
      <p className="gap-note">Not available yet: the console has no per-user sign-in history route. An administrator can see sign-ins in Admin → Audit log (event LOGIN).</p>
    </Card>
  );
}
