"""The TS 28.312 Intent NRM datatypes (`specs/5G_APIs/TS28312_IntentNrm.yaml` and the five expectation-family files) as strict pydantic models, with the
expectation-family check (docs/STANDARDS.md decision D-9: strict validation, every caller migrated).

What it is: the spec's names in camelCase, closed enums as `Literal` types, required fields, and `extra="forbid"` on every model (`_Spec`). On top of the
structure, `IntentExpectation` checks each target and context against its family (`ts28312_families.py`, generated from the spec): a known specialised
name must use one of its own allowed conditions and a matching value; any other name is the generic ExpectationTarget / Context, as the families' own
`oneOf` lists allow, and its value must be a `ValueRangeType` (`ts28312_datatypes.py`). DN-typed attributes carry plain strings (this build's ids or
managed-object references); that addressing is the one recorded deviation.

Where it sits: `main.py` uses these classes as the request and report models of its routes (`CreateIntentRequest`, `IntentReportRequest`,
`RegisterRmihRequest`, ...) and as stored shape (`dump`), and reads `PURPOSE_NEEDS` and `REPORT_TYPE_OF`. Design record: `intent-service/README.md` (1.2, 2.1).

Owns: what a well-formed intent, report or handling function looks like. Does not own: whether a function can handle an intent (`main.py`'s capability
check) or any storage.

Before editing: these models are published in `docs/openapi/intent-service.json`, so a change (including a docstring) makes
`tests_integration/test_openapi_specs.py` fail until `scripts/generate_openapi_specs.py` is rerun; that is why the notes on the classes are `#` comments.
Field names with a spelling error (`notFullfilledState`, `couter`, `expectaitonId`) are the spec's own and must stay.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .ts28312_datatypes import named_datatype_problem, reporting_condition_problem, value_range_problem
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

# A ValueRangeType value is not typed here: the structure is checked by `ts28312_datatypes.value_range_problem`, called from `_check_specialised`, because
# the allowed shapes depend on the target or context name.
ValueRange = Any  # ValueRangeType: scalars, lists, or the structured datatypes of ts28312_datatypes.py (checked in IntentExpectation._family)


# Base of every spec model: `extra="forbid"` (an attribute the spec does not name is a 422) and `dump()`, the stored / sent form without None fields.
class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def dump(self) -> dict:
        return self.model_dump(exclude_none=True)


def dump(value):
    """Returns the stored form of a spec model, a list of them, or any other value: models become dicts without None fields (`_Spec.dump`), other values pass through.

    None stays None, which is how an optional attribute stays a SQL NULL.
    """
    if value is None:
        return None
    if isinstance(value, list):
        return [v.dump() if isinstance(v, _Spec) else v for v in value]
    return value.dump() if isinstance(value, _Spec) else value


# ---------------------------------------------------------------- expectation structure

# TS 28.312 Context: an attribute name, a Condition and a ValueRangeType value; `contextInvariant` defaults to false.
class Context(_Spec):
    contextAttribute: str
    contextCondition: Condition
    contextValueRange: ValueRange
    contextInvariant: bool = False


# TS 28.312 ExpectationTarget: a target name, Condition and ValueRangeType value, with optional contexts and a `preferenceWeight` of 0..10.
class ExpectationTarget(_Spec):
    targetName: str
    targetCondition: Condition
    targetValueRange: ValueRange
    contextSelectivity: Selectivity | None = None
    targetContexts: list[Context] | None = None
    preferenceWeight: int | None = Field(default=None, ge=0, le=10)


# TS 28.312 ExpectationObject: what the expectation is about (`objectType`, `objectInstance`, `objectContexts`); all optional here.
# `objectType` selects the expectation family that `IntentExpectation._family` checks against.
class ExpectationObject(_Spec):
    objectType: ObjectType | None = None
    objectInstance: str | None = None
    objectContexts: list[Context] | None = None


# TS 28.312 IntentExpectation: an id, an object and at least one target, with optional contexts, guarantee periods and a weight. `expectationVerb` is a
# free string (DELIVER, ENSURE, MAINTAIN, or a vendor extension). Validated against its family on creation (`_family`).
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
        """Checks every target and context of the expectation against the family of its object type; raises `ValueError` (so the API answers 422) on the first
        violation.

        Target contexts are checked against the family's contexts plus `GENERIC_CONTEXTS`. The expectation contexts, object contexts and guarantee periods are
        checked the same way. An object type with no family (or none given) has no specialised targets, so only the generic rules apply.
        """
        family: dict[str, Any] | None = FAMILIES.get(self.expectationObject.objectType or "")
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
    """Raises `ValueError` when a target or context (`kind`, `name`, `condition`, `value`) breaks its rules in `specialised`.

    A name that `specialised` does not list is the generic target or context: its value must be a ValueRangeType. A listed name must use one of its
    allowed conditions (when the family lists any), a value matching the family's simplified schema, and, for the names in
    `ts28312_datatypes.NAMED_DATATYPES`, a value of that datatype. Note that a family table can list a condition that cannot equal any `Condition`
    value (the generator keeps the spec's spelling), which makes that name impossible to satisfy.
    """
    spec = specialised.get(name)
    if spec is None:
        # the generic ExpectationTarget/Context: its value is a ValueRangeType
        problem = value_range_problem(value)
        if problem:
            raise ValueError(f"{kind} {name!r} value: {problem}")
        return
    if spec["conditions"] and condition not in spec["conditions"]:
        raise ValueError(f"{kind} {name!r} allows condition {spec['conditions']}, not {condition!r}")
    problem = _value_problem(value, spec["value"]) or named_datatype_problem(name, value)
    if problem:
        raise ValueError(f"{kind} {name!r} value: {problem}")


# JSON-schema type name -> the Python types accepted for it, used by `_value_problem` (a bool is separately refused where an integer or number is expected).
_TYPES = {"integer": (int,), "number": (int, float), "string": (str,), "boolean": (bool,), "array": (list,), "object": (dict,)}


def _value_problem(value, schema: dict) -> str | None:
    """Returns the reason `value` does not satisfy the simplified JSON schema `schema`, or None.

    Checks `oneOf` (any alternative), `type`, `enum`, `minimum` / `maximum` for numbers, and `items` for lists; every other schema keyword is ignored, and an
    empty schema accepts anything. This is the value check for the family tables, whose schemas are generated in that reduced form.
    """
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

# TS 28.312 IntentReportControl: where and when reports go. `observationPeriod` is required; `reportRecipientAddress` is the callback that receives
# `notifyIntentReport`; `expectedReportTypes` limits which report kinds it receives (empty means all). `reportingConditions` are checked by
# `ts28312_datatypes.reporting_condition_problem`.
class IntentReportControl(_Spec):
    reportRecipientAddress: str | None = None
    observationPeriod: int  # required by the spec
    expectedReportTypes: list[ExpectedReportType] | None = None
    reportingConditions: list[dict] | None = None  # ReportingCondition (TimeCondition | TargetFulfilmentCondition)
    reportingTargets: list[str] | None = None

    @field_validator("reportingConditions")
    @classmethod
    def _reporting_conditions(cls, conditions):
        """Field validator: raises `ValueError` with the reason when any entry of `reportingConditions` is neither a TimeCondition nor a TargetFulfilmentCondition."""
        for condition in conditions or []:
            problem = reporting_condition_problem(condition)
            if problem:
                raise ValueError(problem)
        return conditions


# TS 28.312 IntentTraceabilityInfo: links a decomposed expectation to the handling function and intent that took it.
class IntentTraceabilityInfo(_Spec):
    intentHandlingFunctionID: str | None = None
    intentID: str | None = None
    decomposedExpectationID: str | None = None


# TS 28.312 IntentHandlingInfo: whether to include trace information (default true) and the traceability list.
class IntentHandlingInfo(_Spec):
    includeTraceInfo: bool = True
    intentTraceabilityInfoList: list[IntentTraceabilityInfo] | None = None


# TS 28.312 IntentInterpretationAssistanceInfo: free-text hints from a previous exchange; all optional.
class IntentInterpretationAssistanceInfo(_Spec):
    dateTime: str | None = None
    conditions: str | None = None
    prevProducerMessage: str | None = None
    prevConsumerMessage: str | None = None


# ---------------------------------------------------------------- IntentReport

# TS 28.312 FulfilmentInfo: FULFILLED or NOT_FULFILLED and, when not fulfilled, a state (RECEIVED, DEGRADED, SUSPENDED, TERMINATED) and reasons.
# `notFullfilledState` is the spec's own misspelling.
class FulfilmentInfo(_Spec):
    fulfilmentStatus: FulfilmentStatus
    notFullfilledState: NotFulfilledState | None = None  # sic — the spec's own spelling
    notFulfilledReasons: list[str] | None = None


# TS 28.312 Distribution: one bin of a fulfilment statistic. `couter` is the spec's own misspelling.
class Distribution(_Spec):
    couter: int | None = None  # sic
    bin: str | None = None


# TS 28.312 FulfilmentStatisticsInfo: object-wise and time-wise distributions of fulfilment.
class FulfilmentStatisticsInfo(_Spec):
    expectationObjectFulfilmentInfo: list[Distribution] | None = None
    expectationTemporalFulfilmentInfo: list[Distribution] | None = None


# TS 28.312 TargetFulfilmentResult: the fulfilment of one target, with the value achieved.
class TargetFulfilmentResult(_Spec):
    targetName: str
    targetFulfilmentInfo: FulfilmentInfo
    targetAchievedValue: ValueRange = None
    targetContexts: list[Context] | None = None


# TS 28.312 ExpectationFulfilmentResult: the fulfilment of one expectation and its targets. `expectaitonId` is the spec's own misspelling.
class ExpectationFulfilmentResult(_Spec):
    expectaitonId: str  # sic — the spec's own spelling
    expectationFulfilmentInfo: FulfilmentInfo
    expectationFulfilmentStatisticsInfo: FulfilmentStatisticsInfo | None = None
    targetFulfilmentResults: list[TargetFulfilmentResult] | None = None
    guaranteeConfidenceLevel: int | None = Field(default=None, ge=0, le=100)


# TS 28.312 IntentFulfilmentReport: the fulfilment of the whole intent and, optionally, of each expectation.
class IntentFulfilmentReport(_Spec):
    intentFulfilmentInfo: FulfilmentInfo
    expectationFulfilmentResult: list[ExpectationFulfilmentResult] | None = Field(default=None, min_length=1)
    additionalFulfilmentInfo: str | None = None
    guaranteeConfidenceLevel: int | None = Field(default=None, ge=0, le=100)


# TS 28.312 IntentConflictReport: one conflict, of type INTENT_CONFLICT, EXPECTATION_CONFLICT or TARGET_CONFLICT, with a MODIFY or DELETE recommendation.
class IntentConflictReport(_Spec):
    conflictId: str
    conflictType: Literal["INTENT_CONFLICT", "EXPECTATION_CONFLICT", "TARGET_CONFLICT"]
    conflictingIntent: str | None = None
    conflictingExpectation: str | None = None
    conflictingTarget: str | None = None
    recommendedSolutions: Literal["MODIFY", "DELETE"] | None = None


# TS 28.312 InFeasibleTargetInfo: a target that cannot be met, with an optional recommended value.
class InFeasibleTargetInfo(_Spec):
    targetName: str | None = None
    recommendedValue: float | None = None


# TS 28.312 InFeasibleExpectationInfo: an expectation and the targets of it that cannot be met.
class InFeasibleExpectationInfo(_Spec):
    expectationId: str
    inFeasibleTargets: list[InFeasibleTargetInfo]


# TS 28.312 IntentFeasibilityCheckReport: FEASIBLE or INFEASIBLE, the reasons, and the infeasible expectations. `infeasibilityReasons` is required (empty when feasible).
class IntentFeasibilityCheckReport(_Spec):
    feasibilityCheckResult: Literal["FEASIBLE", "INFEASIBLE"]
    infeasibilityReasons: list[Literal["INVALID_INTENT_EXPRESSION", "INTENT_CONFLICT"]]
    inFeasibleExpectationInfos: list[InFeasibleExpectationInfo] | None = None
    additionalPreEvaluationInfo: str | None = None


# TS 28.312 TargetExplorationResult: a target and the value range found for it by exploration.
class TargetExplorationResult(_Spec):
    targetName: str | None = None
    targetCondition: Condition | None = None
    targetValueRange: ValueRange = None
    cellContext: dict | None = None
    coverageAreaPolygonContext: dict | None = None


# TS 28.312 ExpectationExplorationResult: exploration results of one expectation's targets and contexts.
class ExpectationExplorationResult(_Spec):
    expectationId: str
    targetExplorationResults: list[TargetExplorationResult] = Field(min_length=1)
    contextExplorationResults: list[Context] | None = Field(default=None, min_length=1)


# TS 28.312 IntentExplorationReport: the exploration results and the exploration status.
class IntentExplorationReport(_Spec):
    expectationExplorationResults: list[ExpectationExplorationResult] | None = Field(default=None, min_length=1)
    additionalPreEvaluationInfo: str | None = None
    expectationExplorationStatus: Literal["NOT_STARTED", "RUNNING", "FINISHED", "FAILED"] | None = None


# TS 28.312 UtilityResult: the value of one utility function.
class UtilityResult(_Spec):
    utilityFunctionId: str | None = None
    utilityResult: float | None = None


# TS 28.312 IntentUtilityReport: a list of utility results.
class IntentUtilityReport(_Spec):
    utilityResultList: list[UtilityResult] | None = None


# TS 28.312 PossibleImpact: the objects and attributes an outcome would affect.
class PossibleImpact(_Spec):
    impactedObjects: list[str] | None = Field(default=None, min_length=1)
    impactedAttributes: list[dict] | None = Field(default=None, min_length=1)


# TS 28.312 PossibleIntentOutcome: one outcome offered in a negotiation, identified by an integer id the consumer answers with.
class PossibleIntentOutcome(_Spec):
    possibleIntentOutcomeId: int
    intentFulfilmentInfo: FulfilmentInfo
    expectationFulfilmentResults: list[ExpectationFulfilmentResult] | None = Field(default=None, min_length=1)
    possibleImpacts: list[PossibleImpact] | None = Field(default=None, min_length=1)


# TS 28.312 IntentFulfilmentNegotiationFeedback: the consumer's answer to a negotiation, the outcome it picked and an optional satisfaction index.
class IntentFulfilmentNegotiationFeedback(_Spec):
    referredIntentOutcomeId: int | None = None
    consumerSatisfactionIndex: int | None = None


# TS 28.312 ImplicitIntent: expectations and contexts the handling function adds on its own.
class ImplicitIntent(_Spec):
    implicitIntentExpectations: list[IntentExpectation] | None = None
    implicitIntentContexts: list[Context] | None = None


# TS 28.312 IntentFulfilmentNegotiationReport: the outcomes on offer, the consumer's feedback once given (written by `POST /intents/{id}/negotiation-feedback`) and any implicit intent.
class IntentFulfilmentNegotiationReport(_Spec):
    possibleIntentOutcomeList: list[PossibleIntentOutcome] | None = Field(default=None, min_length=1)
    intentFulfilmentNegotiationConsumerFeedback: IntentFulfilmentNegotiationFeedback | None = None
    implicitIntent: ImplicitIntent | None = None


# TS 28.312 IntentDecompositionReport: the sub-intents an intent was decomposed into.
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

# TS 28.312 SupportedContextInfo: a context a handling function supports (attribute, condition, value range).
class SupportedContextInfo(_Spec):
    supportedContextAttribute: str | None = None
    supportedContextCondition: Condition | None = None
    supportedContextValueRange: ValueRange = None


# TS 28.312 SupportedExpectationTargetInfo: a target a handling function supports. `main.py` treats the name as the capability and, when
# `supportedTargetCondition` is given, the same condition as required; the value range is not compared.
class SupportedExpectationTargetInfo(_Spec):
    supportedTargetName: str
    supportedTargetCondition: Condition | None = None
    supportedTargetValueRange: ValueRange = None


# TS 28.312 IntentHandlingCapability: one expectation object type a handling function supports and the targets and contexts it supports for it.
class IntentHandlingCapability(_Spec):
    intentHandlingCapabilityId: str
    supportedExpectationObjectType: SupportedObjectType
    supportedObjectContextInfoList: list[SupportedContextInfo] | None = None
    supportedExpectationTargetInfoList: list[SupportedExpectationTargetInfo]
    supportedExpectationContextInfoList: list[SupportedContextInfo] | None = None


# TS 28.312 UtilityParameter: a parameter name and its weight in a utility.
class UtilityParameter(_Spec):
    parameterName: str | None = None
    parameterWeight: float | None = None


# TS 28.312 UtilityDefinition: a utility a handling function can compute.
class UtilityDefinition(_Spec):
    utilityDefinitionId: str | None = None
    utilityDescription: str | None = None
    utilityParameterList: list[UtilityParameter] | None = None
