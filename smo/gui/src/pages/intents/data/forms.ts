/** The TS 28.312 vocabulary the Intents page's two forms share (new intent, autonomy dispatch): purposes, expectation object types, a
 * spec-valid default target per object type, and how one expectation is built from the form. Pure constants and one builder. */

/** TS 28.312 intentMgmtPurpose values. */
export const PURPOSES = ["FULFILMENT_WITHOUT_NEGOTIATION", "FULFILMENT_WITH_NEGOTIATION", "FEASIBILITYCHECK", "FEASIBILITYCHECK_WITH_RECOMMENDATIONS", "EXPLORATION"];
/** The expectation object types Intent Service accepts. */
export const OBJECT_TYPES = ["RAN_SUBNETWORK", "EDGE_SERVICE_SUPPORT", "5GC_SUBNETWORK", "RADIO_SERVICE", "SUBNETWORK"];

/** A spec-valid default target per expectation family (TS 28.312: each family fixes its targets' conditions/value ranges). */
export const DEFAULT_TARGETS: Record<string, string> = {
  RAN_SUBNETWORK: '[{"targetName": "RANEnergyConsumption", "targetCondition": "IS_LESS_THAN", "targetValueRange": 500}]',
  RADIO_SERVICE: '[{"targetName": "DlThptPerUE", "targetCondition": "IS_GREATER_THAN", "targetValueRange": 50}]',
  "5GC_SUBNETWORK": '[{"targetName": "Latency", "targetCondition": "IS_LESS_THAN", "targetValueRange": 20}]',
  EDGE_SERVICE_SUPPORT: '[{"targetName": "DlLatency", "targetCondition": "IS_LESS_THAN", "targetValueRange": 20}]',
  SUBNETWORK: '[{"targetName": "MaintenanceVersion", "targetCondition": "IS_EQUAL_TO", "targetValueRange": "1.0"}]',
};

/** One TS 28.312 IntentExpectation from the form's object type + targets. */
export function expectationOf(objectType: string, targets: unknown[]) {
  return { expectationId: "e1", expectationVerb: "DELIVER", expectationObject: { objectType }, expectationTargets: targets };
}

/** The targets text parsed as a JSON array, or null when it is not one. */
export function parseTargets(text: string): unknown[] | null {
  try { const v = JSON.parse(text); return Array.isArray(v) ? v : null; } catch { return null; }
}
