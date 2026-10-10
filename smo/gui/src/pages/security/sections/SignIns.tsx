/** Recent sign-ins (`security.signins`, handoff `Security.dc.html`): the signed-in user's own newest sign-ins, failed sign-ins and sign-outs
 * from the BFF's audit log (`GET /api/me/sign-ins?limit=20`, GUI-9.8; never another user's rows), newest first, with what the BFF recorded about
 * each. A failed sign-in the user does not recognise is the reason to change the password; the box says so when there is one. */
import { DataTable, Card } from "../../../components/ui";
import { Badge } from "../../../kit/Badge";
import { Callout } from "../../../kit/Callout";
import { formatTime } from "../../../lib/domain";
import { SIGN_IN_LIMIT, useSignIns } from "../data/queries";

/** The badge tone and label of an audit action. */
export function actionLabel(action: string): { tone: "ok" | "bad" | "mute" | "info"; text: string } {
  if (action === "LOGIN") return { tone: "ok", text: "signed in" };
  if (action === "LOGIN_FAILED") return { tone: "bad", text: "failed sign-in" };
  if (action === "LOGOUT") return { tone: "mute", text: "signed out" };
  if (action === "TOKEN") return { tone: "info", text: "token issued" };
  return { tone: "mute", text: action.toLowerCase().replace(/_/g, " ") };
}

/** The box. */
export function SignIns() {
  const rows = useSignIns();
  const failed = (rows.data ?? []).filter((r) => r.action === "LOGIN_FAILED").length;
  return (
    <Card section="security.signins" title="Recent sign-ins" sub={`your newest ${SIGN_IN_LIMIT} · from the console's audit log`}>
      {failed > 0 && <Callout tone="warn" title={`${failed} failed sign-in${failed === 1 ? "" : "s"} among these`}>If you do not recognise one, change your password.</Callout>}
      <DataTable rows={rows.data?.map((r, i) => ({ ...r, key: `${r.at}-${i}` }))} loading={rows.isLoading} error={rows.data ? undefined : rows.error}
        rowKey={(r) => r.key} empty="No sign-in recorded yet." columns={[
          { header: "When", render: (r) => <span className="small">{formatTime(r.at)}</span> },
          { header: "What", render: (r) => { const a = actionLabel(r.action); return <Badge tone={a.tone}>{a.text}</Badge>; } },
          { header: "Detail", render: (r) => r.detail ? <span className="small muted">{r.detail}</span> : <span className="muted">—</span> },
        ]} />
    </Card>
  );
}
