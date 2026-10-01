# TS 28.312 compliance matrix — Intent Service

**Wave 6** (`docs/roadmap/WAVES_4_TO_10_WORK_ITEMS.md`, W6-01..W6-05, decision D-9; agreed: strict
validation, every caller migrated). Generated from `specs/5G_APIs/TS28312_IntentNrm.yaml`
(91 rows); the expectation families come from the five `TS28312_*Expectation.yaml` files.

## Summary

| | |
|---|---|
| IOCs | Intent, IntentReport, IntentHandlingFunction and IntentUtilityFormula, all REST resources |
| Rows | 91: 83 compliant, 8 partial |
| Partial | UEGroup, QoSId, CivicArea, CivicAddress, Frequency (ValueRangeType members) and the ReportingCondition / TimeCondition / TargetFulfilmentCondition forms. These are accepted as values, but their inner structure isn't enforced. |
| Recorded deviation | Addressing: id references, no DN tree |

**Strict.** `POST /intents` and `POST /autonomy-dispatches` accept only spec-valid intents:
- required `userLabel`, `intentExpectations` (≥1) and `intentReportControl` (with `observationPeriod`)
- each expectation needs an `expectationId`, an `expectationObject` and ≥1 target
- closed enums throughout

Callers migrated in the same PR: the GUI, the SDK, DEMO_RUNBOOK, the demo integration test and
AutonomyDispatch.

**Expectation families.** `scripts/generate_ts28312_families.py` turns the five family specs into
`intent-service/app/ts28312_families.py`: 34 specialised targets and 41 specialised contexts. A
known specialised name must use its own allowed condition and value range; for example,
`AveDLPrbLoad` is `IS_LESS_THAN` with an integer 0..100. Any other name is the generic
ExpectationTarget/Context, exactly as the families' oneOf lists allow.

**Behaviour at creation:**
1. Every expectation object type must have a capability on the addressed RMIH.
2. A purpose that needs negotiation (FEASIBILITY_CHECK / EXPLORATION / FULFILMENT_WITH_NEGOTIATION)
   must be one of the RMIH's `supportedNegotiationFunctionalities`, when it declares any.
3. Target feasibility is checked against the capability's `supportedExpectationTargetInfoList`.
   A FEASIBILITYCHECK* intent is accepted with an INFEASIBLE `intentFeasibilityCheckReport`; a
   fulfilment intent with an infeasible target is rejected.
4. A TARGET_CONFLICT is reported when another ACTIVATED intent sets the same target on the same
   object instance differently.
5. The initial IntentReport (NOT_FULFILLED / RECEIVED) becomes `intentReportReference`.

Reports of every kind are delivered to each `intentReportControl.reportRecipientAddress` whose
`expectedReportTypes` they match. Deactivation reports SUSPENDED. A consumer answers a
negotiation report with `POST /intents/{id}/negotiation-feedback`.

**W6-03.** The energy-saving template is `sdk.intent.energy_saving_expectation(...)`: a
RadioNetworkExpectation with a `RANEnergyConsumption` target, an optional Cell context and a daily
`schedulingTime` guarantee period. It is validated by the service's own model.

**W6-04.** `IntentFulfilmentReport` (with `additionalFulfilmentInfo` for the downstream action
references) is validated and stored. The handler that enacts O1 changes and writes those
references is the generic O1-CM RMIH in Wave 8 (W8-07).

## Matrix

### Intent

Resource: `/intent-service/intents`

| Attribute / child | Status | Note |
|---|---|---|
| `userLabel` | Compliant |  |
| `intentExpectations` | Compliant | Strict: IntentExpectation or a family specialisation (Radio Network / Radio Service / 5GC / Edge / Network Maintenance); specialised targets/contexts checked against generated family constraints |
| `intentMgmtPurpose` | Compliant | FEASIBILITYCHECK* → feasibility report; purposes needing negotiation require the RMIH's supportedNegotiationFunctionalities |
| `contextSelectivity` | Compliant |  |
| `consumerSatisfactionIndexThreshold` | Compliant |  |
| `expectationSelectivity` | Compliant |  |
| `intentContexts` | Compliant |  |
| `intentAdminState` | Compliant | DEACTIVATED → report NOT_FULFILLED/SUSPENDED |
| `intentPriority` | Compliant |  |
| `intentPreemptionCapability` | Compliant |  |
| `intentReportControl` | Compliant | Required; drives report delivery (reportRecipientAddress × expectedReportTypes) |
| `implicitIntentIndex` | Compliant |  |
| `guaranteePeriods` | Compliant |  |
| `intentHandlingInfo` | Compliant |  |
| `intentInterpretationAssistanceInfo` | Compliant |  |
| `intentReportReference` | Compliant | Read-only: the latest IntentReport (initial RECEIVED report written at creation) |
| `intentUtilityFormulaRef` | Compliant | Validated against IntentUtilityFormula |

### IntentReport

Resource: `/intent-service/intent-reports`

| Attribute / child | Status | Note |
|---|---|---|
| `intentFulfilmentReport` | Compliant |  |
| `intentConflictReports` | Compliant | Also computed at creation (TARGET_CONFLICT vs other ACTIVATED intents on the same objectInstance) |
| `intentFeasibilityCheckReport` | Compliant | Also computed by Intent Service from the RMIH's IntentHandlingCapability |
| `intentExplorationReport` | Compliant |  |
| `intentUtilityReports` | Compliant |  |
| `intentFulfilmentNegotiationReport` | Compliant | Consumer feedback via POST /intents/{id}/negotiation-feedback |
| `intentDecompositionReport` | Compliant |  |
| `lastUpdatedTime` | Compliant | Set on write / negotiation feedback |
| `intentReference` | Compliant | = intent id |

### IntentHandlingFunction

Resource: `/intent-service/intent-handling-functions`

| Attribute / child | Status | Note |
|---|---|---|
| `intentHandlingScope` | Compliant |  |
| `supportedNegotiationFunctionalities` | Compliant |  |
| `intentHandlingCapabilityList` | Compliant | Strict IntentHandlingCapability; drives object-type, target feasibility checks |
| `supportedUtilityList` | Compliant |  |
| `Intent` | Compliant (containment as reference) | Containment: Intent.rmihId (ON DELETE CASCADE) |
| `IntentReport` | Compliant (containment as reference) | Containment: via the Intent |
| `IntentUtilityFormula` | Compliant (containment as reference) | Containment: referenced from Intent.intentUtilityFormulaRef |

### IntentUtilityFormula

Resource: `/intent-service/intent-utility-formulas`

| Attribute / child | Status | Note |
|---|---|---|
| `utilityFunctionId` | Compliant |  |
| `utilityParameterList` | Compliant |  |
| `utilityScale` | Compliant |  |
| `utilityOffset` | Compliant |  |

### Datatypes

| Datatype | Status |
|---|---|
| `IntentExpectation` | Compliant |
| `ExpectationObject` | Compliant |
| `Condition` | Compliant |
| `Selectivity` | Compliant |
| `IntentMgmtPurpose` | Compliant |
| `FulfilmentStatus` | Compliant |
| `NotFulfilledState` | Compliant |
| `FulfilmentInfo` | Compliant |
| `FulfilmentStatisticsInfo` | Compliant |
| `Distribution` | Compliant |
| `ExpectationVerb` | Compliant |
| `Frequency` | Partial — accepted as a value; inner structure not enforced (family constraints still apply where a family specialises it) |
| `ValueRangeType` | Compliant |
| `UEGroup` | Partial — accepted as a value; inner structure not enforced (family constraints still apply where a family specialises it) |
| `QoSId` | Partial — accepted as a value; inner structure not enforced (family constraints still apply where a family specialises it) |
| `IntentHandlingScope` | Compliant |
| `NegotiationFunctionality` | Compliant |
| `CivicArea` | Partial — accepted as a value; inner structure not enforced (family constraints still apply where a family specialises it) |
| `CivicAddress` | Partial — accepted as a value; inner structure not enforced (family constraints still apply where a family specialises it) |
| `IntentHandlingInfo` | Compliant |
| `ExpectationTarget` | Compliant |
| `Context` | Compliant |
| `IntentReportControl` | Compliant |
| `ExpectedReportType` | Compliant |
| `ReportingCondition` | Partial — accepted as a value; inner structure not enforced (family constraints still apply where a family specialises it) |
| `TimeCondition` | Partial — accepted as a value; inner structure not enforced (family constraints still apply where a family specialises it) |
| `TargetFulfilmentCondition` | Partial — accepted as a value; inner structure not enforced (family constraints still apply where a family specialises it) |
| `IntentFulfilmentReport` | Compliant |
| `ExpectationFulfilmentResult` | Compliant |
| `TargetFulfilmentResult` | Compliant |
| `IntentConflictReport` | Compliant |
| `IntentUtilityReport` | Compliant |
| `IntentFeasibilityCheckReport` | Compliant |
| `InFeasibleExpectationInfo` | Compliant |
| `InFeasibleTargetInfo` | Compliant |
| `IntentExplorationReport` | Compliant |
| `ExpectationExplorationResult` | Compliant |
| `TargetExplorationResult` | Compliant |
| `IntentFulfilmentNegotiationReport` | Compliant |
| `PossibleIntentOutcome` | Compliant |
| `PossibleImpact` | Compliant |
| `IntentFulfilmentNegotiationFeedback` | Compliant |
| `ImplicitIntent` | Compliant |
| `IntentHandlingCapability` | Compliant |
| `SupportedExpectationTargetInfo` | Compliant |
| `SupportedContextInfo` | Compliant |
| `UtilityParameter` | Compliant |
| `UtilityResult` | Compliant |
| `UtilityDefinition` | Compliant |
| `IntentDecompositionReport` | Compliant |
| `IntentTraceabilityInfo` | Compliant |
| `IntentInterpretationAssistanceInfo` | Compliant |
| `DecompositionAssistingContext` | Compliant |
| `SchedulingTimeContext` | Compliant |
