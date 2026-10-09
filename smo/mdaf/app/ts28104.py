"""TS 28.104 MDA NRM and MDA report datatypes (`specs/5G_APIs/TS28104_MdaNrm.yaml`, `TS28104_MdaReport.yaml`) as request-validation
models; `docs/STANDARDS.md` decision D-9.

Used by `mda.py` to validate MDA function, request and report bodies. Spec camelCase names and closed enums, `extra="forbid"`,
so a field or value outside the spec is a 422. `mDAOutputList` is a oneOf in the spec; here it is validated against the one
output type its own `mDAType` selects (`OUTPUT_TYPE_BY_MDA_TYPE`), or as the generic MDAOutputEntry list, which is allowed for
every MDAType. DN-typed attributes carry plain strings (this build's ids or managed-element references): the one recorded
deviation is addressing. Some enum strings reproduce the spec exactly, including odd spacing, because callers must send them as
the spec does.

Before editing: the list of MDA types is also kept in `ran-analytics/app/models.py` (`MDA_TYPES`); change both together.
"""

import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# The closed MDAType enum of TS28104_MdaNrm.yaml (25 values).
MDA_TYPES = (
    "COVERAGE_ANALYTICS_COVERAGE_PROBLEM_ANALYSIS", "COVERAGE_ANALYTICS_PAGING_OPTIMIZATION",
    "COVERAGE_ANALYTICS_RET_TP_ANALYTICS", "SLS_ANALYSIS_SERVICE_EXPERIENCE_ANALYSIS",
    "SLS_ANALYSIS_NETWORK_SLICE_THROUGHPUT_ANALYSIS", "SLS_ANALYSIS_NETWORK_SLICE_TRAFFIC_ANALYSIS",
    "SLS_ANALYSIS_E2E_LATENCY_ANALYSIS", "SLS_ANALYSIS_NETWORK_SLICE_LOAD_ANALYSIS",
    "UE_THROUGHPUT_ANALYSIS_TRAFFIC_CONGESTION_PROBLEM_ANALYSIS", "SLS_ANALYSIS_EDGE_APPLICATION_DEPLOYMENT_LOCATION_ANALYSIS",
    "SLS_ANALYSIS_EDGE_COMPUTING_PERFORMANCE_ANALYSIS", "SLS_ANALYSIS_TRAFFIC_CONGESTION_PREDICTION_ANALYSIS",
    "MDA_ASSISTED_FAULT_MANAGEMENT_FAILURE_PREDICTION", "MDA_ASSISTED_ENERGY_SAVING_ENERGY_SAVING_ANALYSIS",
    "MOBILITY_MANAGEMENT_ANALYTICS_MOBILITY_PERFORMANCE_ANALYSIS", "MOBILITY_MANAGEMENT_ANALYTICS_HANDOVER_OPTIMIZATION",
    "MAINTENANCE_MAINTENANCE_ANALYTICS", "MAINTENANCE_SOFTWARE_UPGRADE_VALIDATION_ANALYTICS",
    "RESOURCE_ANALYTICS_VIRTUALIZED_RESOURCE_UTILIZATION_ANALYSIS_NF", "RESOURCE_ANALYTICS_PHYSICAL_RESOURCE_UTILIZATION_ANALYSIS_NF",
    "RESOURCE_ANALYTICS_5GC_CONTROL_PLANE_CONGESTION_ANALYSIS", "PREDICTIONS_PM_DATA",
    "ATSSS_PERFORMANCE_TRAFFIC_STEERING_ANALYTICS", "CORRELATION_ANALYTICS_TRAINING_DATA_ANALYSIS",
    "CORRELATION_ANALYTICS_NF_SCALING_DIMENSIONING_DATA_ANALYSIS",
)
MDAType = Literal[MDA_TYPES]  # type: ignore[valid-type]
ReportingMethod = Literal["FILE", "STREAMING", "NOTIFICATION"]
MDADomain = Literal["CN", "RAN", "CROSS_DOMAIN"]
ThresholdDirection = Literal["UP", "DOWN", "UP_AND_DOWN"]
# W5-02 — this build's typing of a report, not a spec attribute.
ReportKind = Literal["ANALYTICS", "PREDICTION", "DRIFT"]
# The MDA types whose reports are inferred to be of kind PREDICTION (`mda._infer_kind`).
PREDICTION_MDA_TYPES = {"PREDICTIONS_PM_DATA", "MDA_ASSISTED_FAULT_MANAGEMENT_FAILURE_PREDICTION",
                        "SLS_ANALYSIS_TRAFFIC_CONGESTION_PREDICTION_ANALYSIS"}


# Base of the datatypes below: `extra="forbid"`, and `dump()` gives the stored form.
class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def dump(self) -> dict:
        """The model as a dict without the unset attributes, the form that is stored and returned."""
        return self.model_dump(exclude_none=True)


def dump(value):
    """`_Spec.dump` for a model, a list of models, or a plain value; None stays None."""
    if value is None:
        return None
    if isinstance(value, list):
        return [v.dump() if isinstance(v, _Spec) else v for v in value]
    return value.dump() if isinstance(value, _Spec) else value


# ---------------------------------------------------------------- MDA NRM

# TS 28.104 ThresholdInfo of an MDA request; `thresholdDirection` is a closed enum and `hysteresis` a non-negative width.
class ThresholdInfo(_Spec):
    monitoredMDAOutputIE: str
    thresholdDirection: ThresholdDirection
    thresholdValue: float
    hysteresis: float = Field(default=0, ge=0)


# TS 28.104 AnalyticsSchedule; stored as sent.
class AnalyticsSchedule(_Spec):
    timeDurations: list[dict] | None = None
    granularityPeriod: int | None = None


# TS 28.104 MDAOutputIEFilter: select reports by an output IE's value (`filterValue`), by a threshold crossing, and until a `timeOut`.
class MDAOutputIEFilter(_Spec):
    mDAOutputIEName: str
    filterValue: str | None = None
    threshold: list[ThresholdInfo] | None = None
    analyticsPeriod: AnalyticsSchedule | None = None
    timeOut: str | None = None  # DateTime

    @field_validator("timeOut")
    @classmethod
    def _timeout_is_a_datetime(cls, value: str | None) -> str | None:
        """Refuses a `timeOut` that is not an ISO 8601 date-time (a trailing Z is accepted). Matching compares it with the clock on every report, so
        a stored value that did not parse would fail those publishes with a 500.
        """
        # The request is matched against reports later and compares this with the clock;
        # a value that does not parse would fail there as a 500, so refuse it here.
        if value:
            try:
                datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError("timeOut must be an ISO 8601 date-time") from exc
        return value


# TS 28.104 MDAOutputPerMDAType: one wanted MDA type with optional IE filters.
class MDAOutputPerMDAType(_Spec):
    mDAType: MDAType
    mDAOutputIEFilters: list[MDAOutputIEFilter] | None = None


class AnalyticsScopeType(_Spec):
    """oneOf in the spec (managedEntitiesScope | areaScope)."""
    managedEntitiesScope: list[str] | None = None
    areaScope: list[dict] | None = None

    @model_validator(mode="after")
    def _exactly_one(self):
        if (self.managedEntitiesScope is None) == (self.areaScope is None):
            raise ValueError("exactly one of managedEntitiesScope/areaScope")
        return self


# TS 28.104 AnalysisRequirement; stored as sent.
class AnalysisRequirement(_Spec):
    timeConstraint: list[dict] | None = None
    resourceConstraint: str | None = None


# ---------------------------------------------------------------- MDA report outputs

# TS 28.104 Recommended3GPPAction (a proposed change to a managed object).
class Recommended3GPPAction(_Spec):
    mOInstance: str | None = None
    path: str | None = None
    op: Literal["ADD", "REMOVE", "REPLACE"] | None = None
    value: list[dict] | None = None
    additionalText: list[str] | None = None


# TS 28.104 RecommendedAction: 3GPP actions, other actions and human-readable actions, with timing.
class RecommendedAction(_Spec):
    recommended3GPPActions: list[Recommended3GPPAction] | None = None
    recommendedNon3GPPActions: list[str] | None = None
    recommendedHumanReadableActions: list[str] | None = None
    actionInterval: int | None = None
    timeWindow: list[dict] | None = None


# TS 28.104 CoverageCharacterization (RSRP and SINR).
class CoverageCharacterization(_Spec):
    rsrp: float | None = None
    sinr: float | None = None


# TS 28.104 RadioEnvironmentMap entry.
class RadioEnvironmentMap(_Spec):
    geoCoordinate: dict | None = None
    coverageCharacterization: CoverageCharacterization | None = None


# Typed output of COVERAGE_ANALYTICS_COVERAGE_PROBLEM_ANALYSIS.
class CoverageProblemAnalysisOutput(_Spec):
    coverageProblemId: str | None = None
    coverageProblemType: Literal["WEAK_COVERAGE", "COVERAGE_HOLE", "PILOT_POLLUTION", "OVERSHOOT_COVERAGE",
                                 "DL_ULCHANNEL_COVERAGE_MISMATCH", "Other"] | None = None
    coverageProblemAreas: dict | None = None
    problematicCells: int | None = None
    recommendedActions: list[RecommendedAction] | None = None
    radioEnvironmentMap: list[RadioEnvironmentMap] | None = None
    cellConfigurations: dict | None = None


# Typed output of MOBILITY_MANAGEMENT_ANALYTICS_MOBILITY_PERFORMANCE_ANALYSIS. The enum strings are the spec's, including its spacing.
class MobilityPerformanceAnalysisOutput(_Spec):
    mobilityPerformanceIssueIdentifier: int | None = None
    mobilityPerformanceIssueType: Literal["NSA_ISSUE", "SA_ISSUE"] | None = None
    mobilityPerformanceIssue: Literal["TOO-EARLY_HANDOVER", "TOO-LATE_HANDOVER", "PINGPONG_HANDOVER",
                                      "TOO-EARLY_PSCELL_CHANGE", "TOO-LATE_PSCELL_CHANGE",
                                      "TOO-EARLY_PSCELL_ CONDITIONAL_CHANGE", "TOO-LATE_PSCELL_CONDITIONAL_CHANGE"] | None = None
    mobilityPerformanceIssueRootCause: Literal["TOO_LONG_MOBILITY_INTERRUPTION_TIME", "POOR_COVERAGE_OF_THE_CELL_EDGE",
                                               "INAPPROPRIATE_HANDOVER_PARAMETERS", "HIGH_INTERFERENCE",
                                               "INSUFFICIENT TRANSPORT_RESOURCES", "CAPABILITY_ISSUE", "Other"] | None = None
    mobilityPerformanceIssueLocation: dict | None = None
    recommendedActions: list[RecommendedAction] | None = None


# TS 28.104 recommendation of measurement data to collect or skip, with its estimated model performance impact (0 to 100).
class MeasurementDataCorrelationRecommendation(_Spec):
    recommendedMeasurementDataToCollect: list[str] | None = None
    recommendedMeasurementDataNotToCollect: list[str] | None = None
    modelPerformanceImpact: int | None = Field(default=None, ge=0, le=100)


# Typed output of CORRELATION_ANALYTICS_TRAINING_DATA_ANALYSIS.
class TrainingDataAnalysisOutput(_Spec):
    measurementDataCorrelationRecommendation: list[MeasurementDataCorrelationRecommendation] | None = None


# Typed output of CORRELATION_ANALYTICS_NF_SCALING_DIMENSIONING_DATA_ANALYSIS.
class NFScalingDimensioningDataAnalysisOutput(_Spec):
    recommendedActions: list[RecommendedAction] | None = None


# TS 28.104 ThresholdAssessment of a prediction.
class ThresholdAssessment(_Spec):
    performanceMetrics: list[str] | None = None
    timeWindow: dict | None = None
    confidenceScore: float | None = None


# One predicted PM value. `mda.py` flattens these to `pmName` to value for filters and thresholds.
class PmPrediction(_Spec):
    pmName: str
    pmPredictedValue: float
    thresholdAssessment: ThresholdAssessment | None = None


# Typed output of PREDICTIONS_PM_DATA.
class PMDataOutput(_Spec):
    pmPredictions: list[PmPrediction] | None = Field(default=None, min_length=1)
    thresholdAssessment: list[ThresholdAssessment] | None = None
    thresholdAdjustmentRecommendations: list[RecommendedAction] | None = None


# TS 28.104 ManagementDataCollectionInfo (what to collect, from which entities, for how long).
class ManagementDataCollectionInfo(_Spec):
    managementDataType: Literal["MEASUREMENT", "KPI", "TRACE_MDT", "QOE"] | None = None
    managementData: dict | None = None
    targetEntities: list[str] | None = None
    collectionDuration: list[dict] | None = None


# Typed output of MDA_ASSISTED_FAULT_MANAGEMENT_FAILURE_PREDICTION.
class FailurePredictionOutput(_Spec):
    failurePredictionObject: list[str] | None = None
    potentialFailureType: str | None = None
    potentialFailureCause: int | str | None = None
    eventTime: str | None = None
    issueID: str | None = None
    issueDomain: Literal["RAN_ISSUE", "CN_ISSUE", "UNKNOWN"] | None = None
    perceivedSeverity: Literal["CRITICAL", "MAJOR", "MINOR", "WARNING", "INTERMEDIATE", "CLEARED"] | None = None
    trendIndication: Literal["MORE_SEVERE", "NO_CHANGE", "LESS_SEVERE"] | None = None
    predictedFailureEndTime: str | None = None
    statisticsInfoList: list[int] | None = None
    managementDataCollectionRecommendations: list[ManagementDataCollectionInfo] | None = None
    recommendedActions: list[RecommendedAction] | None = None


# From and to times of a projection, as text.
class ProjectionDuration(_Spec):
    fromTime: str | None = None
    toTime: str | None = None


# Typed output of COVERAGE_ANALYTICS_PAGING_OPTIMIZATION.
class PagingOptimizationAnalysisOutput(_Spec):
    oOCDuration: ProjectionDuration | None = None
    oOCLocation: list[dict] | None = Field(default=None, min_length=1)
    oOCMap: list[dict] | None = Field(default=None, min_length=1)


# Typed output of UE_THROUGHPUT_ANALYSIS_TRAFFIC_CONGESTION_PROBLEM_ANALYSIS.
class TrafficCongestionProblemAnalysisOutput(_Spec):
    trafficCongestionId: str | None = None
    trafficCongestionType: Literal["NON_REGULAR_TRAFFIC_CONGESTION", "REGULAR_TRAFFIC_CONGESTION"] | None = None
    timeDuration: list[dict] | None = None
    trafficCongestionAreas: dict | None = None
    recommendedActions: list[RecommendedAction] | None = None
    severityLevel: Literal["SLIGHT_CONGESTION", "MODERATE_CONGESTION", "SEVERE_CONGESTION"] | None = None


# Typed output of COVERAGE_ANALYTICS_RET_TP_ANALYTICS; the capitalised attribute names are the spec's.
class RETTPAnalyticsAnalysisOutput(_Spec):
    isTiltChangeRequired: bool | None = None
    RecommendedTilt: RecommendedAction | None = None
    isTPChangeRequired: bool | None = None
    RecommendedTxPower: RecommendedAction | None = None


# A name and a value: the generic form of an output, allowed for every MDA type.
class MDAOutputEntry(_Spec):
    mDAOutputIEName: str
    mDAOutputIEValue: Any = None


# The MDA types that have a typed output, and the model each one is validated against; any other type must send entry pairs.
OUTPUT_TYPE_BY_MDA_TYPE: dict[str, type[_Spec]] = {
    "COVERAGE_ANALYTICS_COVERAGE_PROBLEM_ANALYSIS": CoverageProblemAnalysisOutput,
    "MOBILITY_MANAGEMENT_ANALYTICS_MOBILITY_PERFORMANCE_ANALYSIS": MobilityPerformanceAnalysisOutput,
    "CORRELATION_ANALYTICS_TRAINING_DATA_ANALYSIS": TrainingDataAnalysisOutput,
    "CORRELATION_ANALYTICS_NF_SCALING_DIMENSIONING_DATA_ANALYSIS": NFScalingDimensioningDataAnalysisOutput,
    "PREDICTIONS_PM_DATA": PMDataOutput,
    "MDA_ASSISTED_FAULT_MANAGEMENT_FAILURE_PREDICTION": FailurePredictionOutput,
    "COVERAGE_ANALYTICS_PAGING_OPTIMIZATION": PagingOptimizationAnalysisOutput,
    "UE_THROUGHPUT_ANALYSIS_TRAFFIC_CONGESTION_PROBLEM_ANALYSIS": TrafficCongestionProblemAnalysisOutput,
    "COVERAGE_ANALYTICS_RET_TP_ANALYTICS": RETTPAnalyticsAnalysisOutput,
}


class MDAOutputs(_Spec):
    """One entry of MDAReport.mDAOutputs."""
    mDAType: MDAType
    mDAOutputList: dict | list[MDAOutputEntry]
    analyticsWindow: dict | None = None
    confidenceDegree: float | None = None

    @model_validator(mode="after")
    def _typed_output(self):
        """Validates a dict-form `mDAOutputList` against the typed output of its MDA type and replaces it with the validated dump (unset
        attributes dropped). A type with no typed output refuses the dict form with the message to use entry pairs. The list form
        passes unchanged.
        """
        if isinstance(self.mDAOutputList, dict):
            output_type = OUTPUT_TYPE_BY_MDA_TYPE.get(self.mDAType)
            if output_type is None:
                raise ValueError(f"{self.mDAType} has no typed output in TS 28.104; send mDAOutputList as MDAOutputEntry pairs")
            self.mDAOutputList = output_type.model_validate(self.mDAOutputList).dump()
        return self

    def entries(self) -> dict:
        """Flattened IE name -> value, for filtering/threshold checks."""
        if isinstance(self.mDAOutputList, list):
            return {e.mDAOutputIEName: e.mDAOutputIEValue for e in self.mDAOutputList}
        flat = dict(self.mDAOutputList)
        for prediction in flat.get("pmPredictions") or []:
            flat[prediction["pmName"]] = prediction["pmPredictedValue"]
        return flat
