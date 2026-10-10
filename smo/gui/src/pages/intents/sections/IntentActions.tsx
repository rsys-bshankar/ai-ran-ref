/** The role-gated buttons of one intent, shared by the table, the cards and the reports drawer: Activate / Deactivate (only on an intent this
 * GUI created: only an intent's creator, RMIO `smo-gui`, may change its admin state) and Delete (retract). */
import type { Intent } from "../../../api/types";
import { ActionButton } from "../../../components/ui";
import { intentPath } from "../data/queries";

/** The buttons. */
export function IntentActions({ intent }: { intent: Intent }) {
  const path = intentPath(intent.intentId);
  const next = intent.intentAdminState === "ACTIVATED" ? "DEACTIVATED" : "ACTIVATED";
  return (
    <div className="row gap end">
      {intent.rmioId === "smo-gui" && <ActionButton label={next === "ACTIVATED" ? "Activate" : "Deactivate"} action={{ method: "PATCH", path: `${path}/admin-state`, json: { newState: next }, success: `Intent ${next}` }} />}
      <ActionButton label="Delete" tone="danger" confirm="Delete (retract) this intent?" action={{ method: "DELETE", path, success: "Intent deleted" }} />
    </div>
  );
}
