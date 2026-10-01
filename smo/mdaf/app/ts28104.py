"""TS 28.104 MDA NRM / MDA report datatypes (specs/5G_APIs/TS28104_MdaNrm.yaml,
TS28104_MdaReport.yaml) as request-validation models — Wave 5,
docs/ROADMAP.md decision D-9.

Spec camelCase names and closed enums, `extra="forbid"`. `mDAOutputList`
is a oneOf in the spec; here it is validated against the one output type
its own `mDAType` selects (`OUTPUT_TYPE_BY_MDA_TYPE`), or as the generic
MDAOutputEntry list, which the spec allows for every MDAType. DN-typed
attributes carry plain strings (this build's ids or managed-element refs):
the one recorded deviation is addressing.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
PREDICTION_MDA_TYPES = {"PREDICTIONS_PM_DATA", "MDA_ASSISTED_FAULT_MANAGEMENT_FAILURE_PREDICTION",
                        "SLS_ANALYSIS_TRAFFIC_CONGESTION_PREDICTION_ANALYSIS"}


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


# ---------------------------------------------------------------- MDA NRM

class ThresholdInfo(_Spec):
    monitoredMDAOutputIE: str
    thresholdDirection: ThresholdDirection
    thresholdValue: float
    hysteresis: float = Field(default=0, ge=0)


class AnalyticsSchedule(_Spec):
    timeDurations: list[dict] | None = None
    granularityPeriod: int | None = None


class MDAOutputIEFilter(_Spec):
    mDAOutputIEName: str
    filterValue: str | None = None
    threshold: list[ThresholdInfo] | None = None
    analyticsPeriod: AnalyticsSchedule | None = None
    timeOut: str | None = None  # DateTime


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


class AnalysisRequirement(_Spec):
    timeConstraint: list[dict] | None = None
    resourceConstraint: str | None = None


# ---------------------------------------------------------------- MDA report outputs

class Recommended3GPPAction(_Spec):
    mOInstance: str | None = None
    path: str | None = None
    op: Literal["ADD", "REMOVE", "REPLACE"] | None = None
    value: list[dict] | None = None
    additionalText: list[str] | None = None


class RecommendedAction(_Spec):
    recommended3GPPActions: list[Recommended3GPPAction] | None = None
    recommendedNon3GPPActions: list[str] | None = None
    recommendedHumanReadableActions: list[str] | None = None
    actionInterval: int | None = None
    timeWindow: list[dict] | None = None


class CoverageCharacterization(_Spec):
    rsrp: float | None = None
    sinr: float | None = None


class RadioEnvironmentMap(_Spec):
    geoCoordinate: dict | None = None
    coverageCharacterization: CoverageCharacterization | None = None


class CoverageProblemAnalysisOutput(_Spec):
    coverageProblemId: str | None = None
    coverageProblemType: Literal["WEAK_COVERAGE", "COVERAGE_HOLE", "PILOT_POLLUTION", "OVERSHOOT_COVERAGE",
                                 "DL_ULCHANNEL_COVERAGE_MISMATCH", "Other"] | None = None
    coverageProblemAreas: dict | None = None
    problematicCells: int | None = None
    recommendedActions: list[RecommendedAction] | None = None
    radioEnvironmentMap: list[RadioEnvironmentMap] | None = None
    cellConfigurations: dict | None = None


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


class MeasurementDataCorrelationRecommendation(_Spec):
    recommendedMeasurementDataToCollect: list[str] | None = None
    recommendedMeasurementDataNotToCollect: list[str] | None = None
    modelPerformanceImpact: int | None = Field(default=None, ge=0, le=100)


class TrainingDataAnalysisOutput(_Spec):
    measurementDataCorrelationRecommendation: list[MeasurementDataCorrelationRecommendation] | None = None


class NFScalingDimensioningDataAnalysisOutput(_Spec):
    recommendedActions: list[RecommendedAction] | None = None


class ThresholdAssessment(_Spec):
    performanceMetrics: list[str] | None = None
    timeWindow: dict | None = None
    confidenceScore: float | None = None


class PmPrediction(_Spec):
    pmName: str
    pmPredictedValue: float
    thresholdAssessment: ThresholdAssessment | None = None


class PMDataOutput(_Spec):
    pmPredictions: list[PmPrediction] | None = Field(default=None, min_length=1)
    thresholdAssessment: list[ThresholdAssessment] | None = None
    thresholdAdjustmentRecommendations: list[RecommendedAction] | None = None


class ManagementDataCollectionInfo(_Spec):
    managementDataType: Literal["MEASUREMENT", "KPI", "TRACE_MDT", "QOE"] | None = None
    managementData: dict | None = None
    targetEntities: list[str] | None = None
    collectionDuration: list[dict] | None = None


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


class ProjectionDuration(_Spec):
    fromTime: str | None = None
    toTime: str | None = None


class PagingOptimizationAnalysisOutput(_Spec):
    oOCDuration: ProjectionDuration | None = None
    oOCLocation: list[dict] | None = Field(default=None, min_length=1)
    oOCMap: list[dict] | None = Field(default=None, min_length=1)


class TrafficCongestionProblemAnalysisOutput(_Spec):
    trafficCongestionId: str | None = None
    trafficCongestionType: Literal["NON_REGULAR_TRAFFIC_CONGESTION", "REGULAR_TRAFFIC_CONGESTION"] | None = None
    timeDuration: list[dict] | None = None
    trafficCongestionAreas: dict | None = None
    recommendedActions: list[RecommendedAction] | None = None
    severityLevel: Literal["SLIGHT_CONGESTION", "MODERATE_CONGESTION", "SEVERE_CONGESTION"] | None = None


class RETTPAnalyticsAnalysisOutput(_Spec):
    isTiltChangeRequired: bool | None = None
    RecommendedTilt: RecommendedAction | None = None
    isTPChangeRequired: bool | None = None
    RecommendedTxPower: RecommendedAction | None = None


class MDAOutputEntry(_Spec):
    mDAOutputIEName: str
    mDAOutputIEValue: Any = None


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
