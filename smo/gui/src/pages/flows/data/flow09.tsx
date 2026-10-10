/** Flow 09 board data (intent registration → fulfilment reporting → admin state): the subject is an intent. Loads the intents, the intent
 * handlers and the chosen intent's reports. */
import { useSmo } from "../../../api/hooks";
import type { Intent, IntentReport, Rmih } from "../../../api/types";
import { ActionButton } from "../../../components/ui";
import { flow09 } from "../../../lib/flows";
import { go } from "./go";
import { choose, SUBJECT_LIMIT } from "./subjects";
import type { FlowBoardData } from "./types";

/** The board of flow 09 for intent `subjectId`. */
export function useFlow09(subjectId: string | null): FlowBoardData {
  const intents = useSmo<Intent[]>("/intent-service/intents", { limit: SUBJECT_LIMIT });
  const intent = choose(intents.data, subjectId, (i) => i.intentId);
  const intentId = intent?.intentId ?? null;
  const handlers = useSmo<Rmih[]>("/intent-service/intent-handling-functions");
  const reports = useSmo<IntentReport[]>(intentId ? "/intent-service/intent-reports" : null, { intent_id: intentId ?? undefined });
  const label = (i: Intent) => `${i.intentId.slice(0, 8)} · ${i.intentAdminState} · ${i.rmioId || "no RMIO"}`;
  return {
    subjects: intents.data?.map((i) => ({ id: i.intentId, label: label(i) })),
    subjectsError: intents.error, retry: () => void intents.refetch(),
    selected: intent && { id: intent.intentId, label: label(intent) },
    steps: flow09(handlers.data ?? [], intent, reports.data ?? []),
    empty: <>No intents. {go("/policy#intents", "Create one")}</>,
    actions: {
      rmih: go("/policy#handlers", "Register a handler"),
      create: go("/policy#intents", "Create an intent"),
      dispatch: go("/policy#handlers", "Register a handler"),
      report: intentId && <ActionButton label="Publish fulfilment report (as so-smos)" title="Simulates the RMIH's report"
        action={{ method: "POST", path: "/intent-service/intent-reports", json: { intentReference: intentId, intentFulfilmentReport: { intentFulfilmentInfo: { fulfilmentStatus: "FULFILLED" } } }, success: "Report published" }} />,
      admin: intent?.rmioId === "smo-gui"
        ? <ActionButton label="Deactivate" action={{ method: "PATCH", path: `/intent-service/intents/${intentId}/admin-state`, json: { newState: "DEACTIVATED" }, success: "Intent DEACTIVATED" }} />
        : <span className="muted small">Only the creating RMIO ({intent?.rmioId || "—"}) may change the admin state.</span>,
    },
  };
}
