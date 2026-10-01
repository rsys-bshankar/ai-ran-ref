"""TS 28.312 Intent NRM datatypes (specs/5G_APIs/TS28312_IntentNrm.yaml and
the five expectation-family files) as strict request models — Wave 6,
docs/ROADMAP.md decision D-9 (agreed: strict validation, every
caller migrated).

Spec camelCase names, closed enums, required fields and `extra="forbid"`.
On top of the generic structure, every expectation is checked against its
family (`ts28312_families.py`, generated from the spec): a known
specialised target/context name must use one of its own allowed
conditions and a value matching its value range. Names the family doesn't
specialise are valid as the generic ExpectationTarget/Context, exactly as
the families' own oneOf lists allow. DN-typed attributes carry plain
strings (this build's ids or managed-object refs): the one recorded
deviation is addressing.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .ts28312_families import FAMILIES, GENERIC_CONTEXTS

Condition = Literal["IS_EQUAL_TO", "IS_LESS_THAN", "IS_GREATER_THAN", "IS_WITHIN_RANGE", "IS_OUTSIDE_RANGE",
                    "IS_ONE_OF", "IS_NOT_ONE_OF", "IS_EQUAL_TO_OR_LESS_THAN", "IS_EQUAL_TO_OR_GREATER_THAN", "IS_ALL_OF"]
Selectivity = Literal["ALL_OF", "ONE_OF", "ANY_OF"]
IntentMgmtPurpose = Literal["FEASIBILITYCHECK", "FEASIBILITYCHECK_WITH_RECOMMENDATIONS", "FULFILMENT_WITHOUT_NEGOTIATION",
                            "EXPLORATION", "FULFILMENT_WITH_NEGOTIATION"]
ObjectType = Literal["RAN_SUBNETWORK", "EDGE_SERVICE_SUPPORT", "5GC_SUBNETWORK", "RADIO_SERVICE", "SUBNETWORK"]
SupportedObjectType = Literal["RAN_SUBNETWORK", "EDGE_SERVICE_SUPPORT", "5GC_SUBNETWORK", "RADIO_SERVICE"]
IntentAdminState = Literal["ACTIVATED", "DEACTIVATED"]
FulfilmentStatus = Literal["FULFILLED", "NOT_FULFILLED"]
NotFulfilledState = Literal["RECEIVED", "DEGRADED", "SUSPENDED", "TERMINATED"]
ExpectedReportType = Literal["INTENT_FULFILMENT_REPORT", "INTENT_CONFLICT_REPORT", "INTENT_FEASIBILITY_CHECK_REPORT",
                             "INTENT_EXPLORATION_REPORT", "INTENT_FULFILMENT_NEGOTIATION_REPORT",
                             "INTENT_UTILITY_REPORT", "INTENT_DECOMPOSITION_REPORT"]
NegotiationFunctionality = Literal["FEASIBILITY_CHECK", "EXPLORATION", "FULFILMENT_WITH_NEGOTIATION"]
IntentHandlingScope = Literal["RAN", "CN"]
# intentMgmtPurpose -> the negotiation functionality the handling function
# must declare to accept it (when it declares any).
PURPOSE_NEEDS = {"FEASIBILITYCHECK": "FEASIBILITY_CHECK", "FEASIBILITYCHECK_WITH_RECOMMENDATIONS": "FEASIBILITY_CHECK",
                 "EXPLORATION": "EXPLORATION", "FULFILMENT_WITH_NEGOTIATION": "FULFILMENT_WITH_NEGOTIATION"}

ValueRange = Any  # ValueRangeType is a oneOf over scalars and TS 28.623 structures; checked per family below


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def dump(self) -> dict:
        return self.model_dump(exclude_none=True)


def dump(value):
    if value is None:
        return None
    if isinstance(value, list):
        return [v.dump() if isinstance(v, _Spec) else v for v in value]
    return value.dump() if isinstance(value, _Spec) else value


# ---------------------------------------------------------------- expectation structure

class Context(_Spec):
    contextAttribute: str
    contextCondition: Condition
    contextValueRange: ValueRange
    contextInvariant: bool = False


class ExpectationTarget(_Spec):
    targetName: str
    targetCondition: Condition
    targetValueRange: ValueRange
    contextSelectivity: Selectivity | None = None
    targetContexts: list[Context] | None = None
    preferenceWeight: int | None = Field(default=None, ge=0, le=10)


class ExpectationObject(_Spec):
    objectType: ObjectType | None = None
    objectInstance: str | None = None
    objectContexts: list[Context] | None = None


class IntentExpectation(_Spec):
    expectationId: str
    expectationVerb: str | None = None  # DELIVER | ENSURE | MAINTAIN; vendor extensions are allowed
    expectationObject: ExpectationObject
    expectationTargets: list[ExpectationTarget] = Field(min_length=1)
    contextSelectivity: Selectivity | None = None
    expectationContexts: list[Context] | None = None
    preferenceWeight: int | None = Field(default=None, ge=0, le=10)
    guaranteePeriods: list[Context] | None = None  # SchedulingTimeContext

    @model_validator(mode="after")
    def _family(self):
        family = FAMILIES.get(self.expectationObject.objectType or "")
        targets = family["targets"] if family else {}
        contexts = {**GENERIC_CONTEXTS, **(family["contexts"] if family else {})}
        for target in self.expectationTargets:
            _check_specialised("target", target.targetName, target.targetCondition, target.targetValueRange, targets)
            for ctx in target.targetContexts or []:
                _check_specialised("context", ctx.contextAttribute, ctx.contextCondition, ctx.contextValueRange, contexts)
        for ctx in [*(self.expectationContexts or []), *(self.expectationObject.objectContexts or []),
                    *(self.guaranteePeriods or [])]:
            _check_specialised("context", ctx.contextAttribute, ctx.contextCondition, ctx.contextValueRange, contexts)
        return self


def _check_specialised(kind: str, name: str, condition: str, value, specialised: dict) -> None:
    spec = specialised.get(name)
    if spec is None:
        return  # the generic ExpectationTarget/Context — already validated structurally
    if spec["conditions"] and condition not in spec["conditions"]:
        raise ValueError(f"{kind} {name!r} allows condition {spec['conditions']}, not {condition!r}")
    problem = _value_problem(value, spec["value"])
    if problem:
        raise ValueError(f"{kind} {name!r} value: {problem}")


_TYPES = {"integer": (int,), "number": (int, float), "string": (str,), "boolean": (bool,), "array": (list,), "object": (dict,)}


def _value_problem(value, schema: dict) -> str | None:
    """Minimal check of `value` against a simplified JSON schema (type,
    enum, minimum/maximum, items, oneOf). Returns a reason, or None."""
    if not schema:
        return None
    if "oneOf" in schema:
        return None if any(_value_problem(value, s) is None for s in schema["oneOf"]) else "matches no allowed form"
    expected = schema.get("type")
    if expected:
        ok_types = _TYPES.get(expected, (object,))
        if isinstance(value, bool) and expected in ("integer", "number"):
            return f"expected {expected}"
        if not isinstance(value, ok_types):
            return f"expected {expected}"
    if "enum" in schema and value not in schema["enum"]:
        return f"must be one of {schema['enum']}"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            return f"below minimum {schema['minimum']}"
        if "maximum" in schema and value > schema["maximum"]:
            return f"above maximum {schema['maximum']}"
    if isinstance(value, list) and "items" in schema:
        for item in value:
            problem = _value_problem(item, schema["items"])
            if problem:
                return problem
    return None


# ---------------------------------------------------------------- Intent

class IntentReportControl(_Spec):
    reportRecipientAddress: str | None = None
    observationPeriod: int  # required by the spec
    expectedReportTypes: list[ExpectedReportType] | None = None
    reportingConditions: list[dict] | None = None  # ReportingCondition (TimeCondition | TargetFulfilmentCondition)
    reportingTargets: list[str] | None = None


class IntentTraceabilityInfo(_Spec):
    intentHandlingFunctionID: str | None = None
    intentID: str | None = None
    decomposedExpectationID: str | None = None


class IntentHandlingInfo(_Spec):
    includeTraceInfo: bool = True
    intentTraceabilityInfoList: list[IntentTraceabilityInfo] | None = None


class IntentInterpretationAssistanceInfo(_Spec):
    dateTime: str | None = None
    conditions: str | None = None
    prevProducerMessage: str | None = None
    prevConsumerMessage: str | None = None


# ---------------------------------------------------------------- IntentReport

class FulfilmentInfo(_Spec):
    fulfilmentStatus: FulfilmentStatus
    notFullfilledState: NotFulfilledState | None = None  # sic — the spec's own spelling
    notFulfilledReasons: list[str] | None = None


class Distribution(_Spec):
    couter: int | None = None  # sic
    bin: str | None = None


class FulfilmentStatisticsInfo(_Spec):
    expectationObjectFulfilmentInfo: list[Distribution] | None = None
    expectationTemporalFulfilmentInfo: list[Distribution] | None = None


class TargetFulfilmentResult(_Spec):
    targetName: str
    targetFulfilmentInfo: FulfilmentInfo
    targetAchievedValue: ValueRange = None
    targetContexts: list[Context] | None = None


class ExpectationFulfilmentResult(_Spec):
    expectaitonId: str  # sic — the spec's own spelling
    expectationFulfilmentInfo: FulfilmentInfo
    expectationFulfilmentStatisticsInfo: FulfilmentStatisticsInfo | None = None
    targetFulfilmentResults: list[TargetFulfilmentResult] | None = None
    guaranteeConfidenceLevel: int | None = Field(default=None, ge=0, le=100)


class IntentFulfilmentReport(_Spec):
    intentFulfilmentInfo: FulfilmentInfo
    expectationFulfilmentResult: list[ExpectationFulfilmentResult] | None = Field(default=None, min_length=1)
    additionalFulfilmentInfo: str | None = None
    guaranteeConfidenceLevel: int | None = Field(default=None, ge=0, le=100)


class IntentConflictReport(_Spec):
    conflictId: str
    conflictType: Literal["INTENT_CONFLICT", "EXPECTATION_CONFLICT", "TARGET_CONFLICT"]
    conflictingIntent: str | None = None
    conflictingExpectation: str | None = None
    conflictingTarget: str | None = None
    recommendedSolutions: Literal["MODIFY", "DELETE"] | None = None


class InFeasibleTargetInfo(_Spec):
    targetName: str | None = None
    recommendedValue: float | None = None


class InFeasibleExpectationInfo(_Spec):
    expectationId: str
    inFeasibleTargets: list[InFeasibleTargetInfo]


class IntentFeasibilityCheckReport(_Spec):
    feasibilityCheckResult: Literal["FEASIBLE", "INFEASIBLE"]
    infeasibilityReasons: list[Literal["INVALID_INTENT_EXPRESSION", "INTENT_CONFLICT"]]
    inFeasibleExpectationInfos: list[InFeasibleExpectationInfo] | None = None
    additionalPreEvaluationInfo: str | None = None


class TargetExplorationResult(_Spec):
    targetName: str | None = None
    targetCondition: Condition | None = None
    targetValueRange: ValueRange = None
    cellContext: dict | None = None
    coverageAreaPolygonContext: dict | None = None


class ExpectationExplorationResult(_Spec):
    expectationId: str
    targetExplorationResults: list[TargetExplorationResult] = Field(min_length=1)
    contextExplorationResults: list[Context] | None = Field(default=None, min_length=1)


class IntentExplorationReport(_Spec):
    expectationExplorationResults: list[ExpectationExplorationResult] | None = Field(default=None, min_length=1)
    additionalPreEvaluationInfo: str | None = None
    expectationExplorationStatus: Literal["NOT_STARTED", "RUNNING", "FINISHED", "FAILED"] | None = None


class UtilityResult(_Spec):
    utilityFunctionId: str | None = None
    utilityResult: float | None = None


class IntentUtilityReport(_Spec):
    utilityResultList: list[UtilityResult] | None = None


class PossibleImpact(_Spec):
    impactedObjects: list[str] | None = Field(default=None, min_length=1)
    impactedAttributes: list[dict] | None = Field(default=None, min_length=1)


class PossibleIntentOutcome(_Spec):
    possibleIntentOutcomeId: int
    intentFulfilmentInfo: FulfilmentInfo
    expectationFulfilmentResults: list[ExpectationFulfilmentResult] | None = Field(default=None, min_length=1)
    possibleImpacts: list[PossibleImpact] | None = Field(default=None, min_length=1)


class IntentFulfilmentNegotiationFeedback(_Spec):
    referredIntentOutcomeId: int | None = None
    consumerSatisfactionIndex: int | None = None


class ImplicitIntent(_Spec):
    implicitIntentExpectations: list[IntentExpectation] | None = None
    implicitIntentContexts: list[Context] | None = None


class IntentFulfilmentNegotiationReport(_Spec):
    possibleIntentOutcomeList: list[PossibleIntentOutcome] | None = Field(default=None, min_length=1)
    intentFulfilmentNegotiationConsumerFeedback: IntentFulfilmentNegotiationFeedback | None = None
    implicitIntent: ImplicitIntent | None = None


class IntentDecompositionReport(_Spec):
    intentDecompositionResults: list[IntentTraceabilityInfo] | None = None


# Report attribute -> the ExpectedReportType it answers (report delivery filter).
REPORT_TYPE_OF = {
    "intentFulfilmentReport": "INTENT_FULFILMENT_REPORT", "intentConflictReports": "INTENT_CONFLICT_REPORT",
    "intentFeasibilityCheckReport": "INTENT_FEASIBILITY_CHECK_REPORT", "intentExplorationReport": "INTENT_EXPLORATION_REPORT",
    "intentUtilityReports": "INTENT_UTILITY_REPORT", "intentFulfilmentNegotiationReport": "INTENT_FULFILMENT_NEGOTIATION_REPORT",
    "intentDecompositionReport": "INTENT_DECOMPOSITION_REPORT",
}


# ---------------------------------------------------------------- IntentHandlingFunction / IntentUtilityFormula

class SupportedContextInfo(_Spec):
    supportedContextAttribute: str | None = None
    supportedContextCondition: Condition | None = None
    supportedContextValueRange: ValueRange = None


class SupportedExpectationTargetInfo(_Spec):
    supportedTargetName: str
    supportedTargetCondition: Condition | None = None
    supportedTargetValueRange: ValueRange = None


class IntentHandlingCapability(_Spec):
    intentHandlingCapabilityId: str
    supportedExpectationObjectType: SupportedObjectType
    supportedObjectContextInfoList: list[SupportedContextInfo] | None = None
    supportedExpectationTargetInfoList: list[SupportedExpectationTargetInfo]
    supportedExpectationContextInfoList: list[SupportedContextInfo] | None = None


class UtilityParameter(_Spec):
    parameterName: str | None = None
    parameterWeight: float | None = None


class UtilityDefinition(_Spec):
    utilityDefinitionId: str | None = None
    utilityDescription: str | None = None
    utilityParameterList: list[UtilityParameter] | None = None
