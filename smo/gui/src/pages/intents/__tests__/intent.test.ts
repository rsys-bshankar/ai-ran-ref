/** Unit tests of the Intents page's pure TS 28.312 readers (`intents/data/intent.ts`): the newest report of a kind, fulfilment as a share of
 * targets met (and no number without per-target results), conflicts, negotiation outcomes and feedback, expectation lines, and the handler
 * support check of the new-intent form. No DOM, no fetch. Run: `npx vitest run src/pages/intents` from smo/gui. */
import { describe, expect, it } from "vitest";

import type { IntentReport, Rmih } from "../../../api/types";
import { conflicts, expectations, fulfilment, handlerSupport, negotiation, targetNames } from "../data/intent";

const report = (at: string, attrs: Record<string, unknown>): IntentReport => ({ reportId: at, intentId: "i", attributes: { lastUpdatedTime: at, ...attrs } });
const ok = { fulfilmentStatus: "FULFILLED" };
const nok = { fulfilmentStatus: "NOT_FULFILLED", notFullfilledState: "DEGRADED" };

describe("intent report readers", () => {
  // The percent is the share of targets reported FULFILLED in the newest fulfilment report, never an invented figure.
  it("derives the share of targets met from the newest fulfilment report", () => {
    const reports = [
      report("3", { intentConflictReports: [{ conflictId: "c1", conflictType: "INTENT_CONFLICT", conflictingIntent: "x" }] }),
      report("2", { intentFulfilmentReport: { intentFulfilmentInfo: nok, expectationFulfilmentResult: [{ expectaitonId: "e1", expectationFulfilmentInfo: nok, targetFulfilmentResults: [
        { targetName: "a", targetFulfilmentInfo: ok }, { targetName: "b", targetFulfilmentInfo: nok }, { targetName: "c", targetFulfilmentInfo: ok }, { targetName: "d", targetFulfilmentInfo: ok }] }] } }),
      report("1", { intentFulfilmentReport: { intentFulfilmentInfo: ok } }),
    ];
    expect(fulfilment(reports)).toMatchObject({ status: "NOT_FULFILLED", state: "DEGRADED", targetsMet: 3, targetsTotal: 4, pct: 75, at: "2" });
  });

  // A report with only the intent-level status has no percent: the ring must show "—", not 0 or 100.
  it("gives no percent without per-target results", () => {
    expect(fulfilment([report("1", { intentFulfilmentReport: { intentFulfilmentInfo: ok } })])).toMatchObject({ status: "FULFILLED", pct: null });
    expect(fulfilment([])).toBeNull();
  });

  // Conflicts and negotiation come from the newest report that carries them.
  it("reads conflicts and the negotiation", () => {
    const reports = [
      report("2", { intentFulfilmentNegotiationReport: { possibleIntentOutcomeList: [{ possibleIntentOutcomeId: 7, intentFulfilmentInfo: ok }], intentFulfilmentNegotiationConsumerFeedback: { referredIntentOutcomeId: 7, consumerSatisfactionIndex: 70 } } }),
      report("1", { intentConflictReports: [{ conflictId: "c1", conflictType: "TARGET_CONFLICT", conflictingTarget: "t" }] }),
    ];
    expect(conflicts(reports).map((c) => c.conflictId)).toEqual(["c1"]);
    expect(negotiation(reports)).toEqual({ outcomes: [{ possibleIntentOutcomeId: 7, intentFulfilmentInfo: ok }], feedback: { referredIntentOutcomeId: 7, consumerSatisfactionIndex: 70 } });
    expect(negotiation([report("1", {})])).toBeNull();
  });

  // The expectation box reads object type and "name condition value" per target.
  it("summarises the expectations", () => {
    expect(expectations({ intentExpectations: [{ expectationObject: { objectType: "RAN_SUBNETWORK" }, expectationTargets: [{ targetName: "RANEnergyConsumption", targetCondition: "IS_LESS_THAN", targetValueRange: 500 }] }] }))
      .toEqual([{ objectType: "RAN_SUBNETWORK", targets: ["RANEnergyConsumption < 500"] }]);
  });
});

describe("handler support check", () => {
  const handler: Rmih = { rmihId: "so-smos", smeServiceId: "s", notificationDestination: "d", intentHandlingScope: null, attributes: { supportedNegotiationFunctionalities: null, intentHandlingCapabilityList: [
    { intentHandlingCapabilityId: "c", supportedExpectationObjectType: "RAN_SUBNETWORK", supportedExpectationTargetInfoList: [{ supportedTargetName: "RANEnergyConsumption" }] }] } };

  // The form says up front what Intent Service would reject: an unsupported object type or target name.
  it("checks the object type and every target name", () => {
    expect(handlerSupport(handler, "RAN_SUBNETWORK", ["RANEnergyConsumption"])).toEqual({ ok: true, problem: null });
    expect(handlerSupport(handler, "RADIO_SERVICE", []).problem).toContain("does not handle RADIO_SERVICE");
    expect(handlerSupport(handler, "RAN_SUBNETWORK", ["Latency"]).problem).toContain("does not support target Latency");
    expect(handlerSupport(undefined, "RAN_SUBNETWORK", []).ok).toBe(false);
    expect(targetNames([{ targetName: "a" }, { x: 1 }, null])).toEqual(["a"]);
  });
});
