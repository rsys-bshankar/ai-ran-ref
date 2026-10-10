/** The Intents page's API knowledge (STRUCTURE.md rule 4): the Intent Service paths it reads and writes, their query parameters (the OpenAPI
 * `GET` parameters only: `admin_state` on intents, `intent_id` on reports, `status` on autonomy dispatches) and the bounds of its one-shot reads.
 * Sections call the hooks here, never `useSmo` with a raw path. Counts come from the BFF summary "intents" (`intents.ACTIVATED`,
 * `intents.DEACTIVATED`, `intents.total`), never from a list. */
import { POLL, useSmo, useSmoPage } from "../../../api/hooks";
import type { InstanceSummary, Intent, IntentReport, Rmih } from "../../../api/types";
import { useSummary } from "../../../data/summary";

/** Intents (`admin_state`). */
export const INTENTS = "/intent-service/intents";
/** Intent reports, newest first (`intent_id`). */
export const REPORTS = "/intent-service/intent-reports";
/** Registered intent handlers (RMIH). */
export const HANDLERS = "/intent-service/intent-handling-functions";
/** Autonomy dispatches (`status`). */
export const DISPATCHES = "/intent-service/autonomy-dispatches";
/** TS 28.312 intent utility formulas (feature 10). */
export const FORMULAS = "/intent-service/intent-utility-formulas";
/** rApp instances, for the autonomy-dispatch form. */
export const INSTANCES = "/rapp-mgmt/instances";
/** How many reports of one intent a card reads: enough to find the newest of each report kind (a row holds only the kinds published together). */
export const CARD_REPORTS = 20;
/** Intents per page in the cards view (each card reads its reports). */
export const CARDS_PER_PAGE = 6;
/** Handlers are few (SCALE.md: 50 at target); the forms and the support check read them in one call. */
export const HANDLER_LIMIT = 200;

/** One intent's path. */
export const intentPath = (id: string) => `${INTENTS}/${id}`;
/** The negotiation-feedback route of an intent (feature 10). */
export const feedbackPath = (id: string) => `${intentPath(id)}/negotiation-feedback`;

/** The page's true counts. */
export function useIntentSummary() {
  return useSummary("intents");
}

/** One page of intents for the cards view. */
export function useIntentPage(adminState: string, offset: number) {
  return useSmoPage<Intent>(INTENTS, { admin_state: adminState || undefined, limit: CARDS_PER_PAGE, offset });
}

/** The newest reports of one intent (null: no call). */
export function useIntentReports(intentId: string | null, limit = CARD_REPORTS) {
  return useSmo<IntentReport[]>(intentId ? REPORTS : null, { intent_id: intentId ?? undefined, limit });
}

/** Every registered handler (bounded). */
export function useHandlers() {
  return useSmo<Rmih[]>(HANDLERS, { limit: HANDLER_LIMIT }, { refetchInterval: POLL.inventory });
}

/** rApp instances for the dispatch form. */
export function useInstances() {
  return useSmo<InstanceSummary[]>(INSTANCES, { limit: 200 });
}
