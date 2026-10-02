"""TS 28.105 AI/ML NRM datatypes (specs/5G_APIs/TS28105_AiMlNrm.yaml) as
request-validation models — Wave 4, docs/STANDARDS.md decision D-9.

Each class mirrors one spec datatype with the spec's own camelCase names
and closed enums; `extra="forbid"` rejects attributes the spec doesn't
define, so a typo surfaces as a 422 rather than being silently stored.
Values are persisted as their spec-shaped JSON (`dump()`), so what is read
back is exactly what the spec describes.

DN-typed attributes (`Dn`, `DnRo`, `DnListRo`) are carried as this build's
own resource ids (UUIDs): the one recorded deviation is addressing — flat
REST resources, no DN containment tree.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RequestStatus = Literal["NOT_STARTED", "IN_PROGRESS", "SUSPENDED", "FINISHED", "CANCELLED", "CANCELLING"]
MLTrainingType = Literal["INITIAL_TRAINING", "PRE_SPECIALISED_TRAINING", "RE_TRAINING", "FINE_TUNING"]
ActivationStatus = Literal["ACTIVATED", "DEACTIVATED"]
LearningTechnology = Literal["RL", "FL", "DL"]
RLEnvironment = Literal["SIMULATION_ENVIRONMENTS", "REAL_NETWORK_ENVIRONMENTS"]
FLRole = Literal["FL_SERVER", "FL_CLIENT"]
KnowledgeType = Literal["TABLE", "STATISTIC", "REGRESSION"]
# AIMLInferenceName is a oneOf over MDAType / NwdafAnalyticsType /
# NgRanInferenceType / VSExtensionType — all strings, the last
# vendor-extensible — so it's validated only as a non-empty string.
# NgRanInferenceType's own closed values, for reference:
NG_RAN_INFERENCE_TYPES = {"NG_RAN_NETWORK_ENERGY_SAVING", "NG_RAN_LOAD_BALANCING", "NG_RAN_MOBILITY_OPTIMIZATION"}


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def dump(self) -> dict:
        return self.model_dump(exclude_none=True)


def dump(value):
    """Spec model(s) -> JSON-storable value; None passes through."""
    if value is None:
        return None
    if isinstance(value, list):
        return [v.dump() if isinstance(v, _Spec) else v for v in value]
    return value.dump() if isinstance(value, _Spec) else value


class ModelPerformance(_Spec):
    inferenceOutputName: str | None = None
    performanceMetric: str | None = None
    performanceScore: float | None = None
    decisionConfidenceScore: float | None = None


class ProcessMonitorUpdate(_Spec):
    """The writable part of ProcessMonitor — what an execution runtime
    reports back as a run progresses."""
    progressPercentage: int = Field(ge=0, le=100)
    progressStateInfo: str | None = None
    resultStateInfo: str | None = None


class MLKnowledge(_Spec):
    mLKnowledgeName: str | None = None
    knowledgeType: KnowledgeType | None = None
    predictorResponseArray: list[list[str]] | None = None


class SupportedLearningTechnology(_Spec):
    learningTechnologyName: list[LearningTechnology] | None = None
    supportedRLEnvironment: list[RLEnvironment] | None = None
    supportedFLRole: list[FLRole] | None = Field(default=None, min_length=1, max_length=2)
    supportedInferenceNameList: list[str] | None = Field(default=None, min_length=1)


class FLParticipationInfo(_Spec):
    fLRole: FLRole | None = None
    isAvailableForFLTraining: bool = False
    candidateFLClientRefList: list[str] | None = None


class FLClientSelectionCriteria(_Spec):
    minimumAvailableDataSamples: int | None = None
    minimumAvailableTimeDuration: int | None = None
    minimumInterimModelPerformance: list[ModelPerformance] | None = None
    servingGeoArea: dict | None = None
    clientRedundancy: int | None = None
    trainingDataWithOrWithoutOutliers: bool | None = None
    uniformlyDistributedTrainingData: bool | None = None


class FLRequirement(_Spec):
    fLClientSelectionCriteria: FLClientSelectionCriteria | None = None


class EnvironmentScope(_Spec):
    managedEntitiesScope: list[str] | None = None
    areaScope: dict | None = None
    timeWindow: dict | None = None


class RLRequirement(_Spec):
    rLEnvironmentType: list[RLEnvironment] | None = None
    rLEnvironmentScope: list[EnvironmentScope] | None = Field(default=None, min_length=1)
    rLImpactedScope: list[EnvironmentScope] | None = Field(default=None, min_length=1)
    rLPerformanceRequirements: list[dict] | None = None  # TS 28.623 ThresholdInfo


class DataStatisticalProperties(_Spec):
    uniformlyDistributedTrainingData: bool | None = None
    trainingDataWithOrWithoutOutliers: bool | None = None


class DistributedTrainingExpectation(_Spec):
    expectedTrainingTime: int | None = None
    dataSplitIndication: bool | None = None
    suggestedTrainingNodeList: list[str] | None = None


class ClusteringCriteria(_Spec):
    performanceMetric: str | None = None
    taskType: str | None = None
    allowedClusterTrainingTime: dict | None = None
    preferredModelDiversity: str | None = None


class ManagedActivationScope(_Spec):
    """oneOf in the spec (dNList | timeWindow | geoPolygon); accepted as
    any one of the three."""
    dNList: list[str] | None = None
    timeWindow: list[dict] | None = None
    geoPolygon: list[dict] | None = None


class AIMLManagementPolicy(_Spec):
    thresholdList: list[dict] | None = None  # TS 28.623 ThresholdInfo
    managedActivationScope: ManagedActivationScope | None = None


class AvailMLCapabilityReport(_Spec):
    availMLCapabilityReportID: str | None = None
    mLCapabilityVersionId: str | None = None
    expectedPerformanceGains: list[ModelPerformance] | None = None
    mLModelRef: str | None = None


class InferenceOutput(_Spec):
    inferenceOutputId: list[str] | None = None
    aIMLInferenceName: str | None = None
    inferenceOutputTime: list[str] | None = None
    inferencePerformance: ModelPerformance | None = None
    inferenceExplanationInfo: list[str] | None = None
    outputResult: dict | None = None  # AttributeNameValuePairSet


class ImpactedPM(_Spec):
    pMIdentifier: str | None = None


class PotentialImpactInfo(_Spec):
    impactedScope: ManagedActivationScope | None = None
    impactedPM: list[ImpactedPM] | None = None


class FLReportPerClient(_Spec):
    clientRef: str | None = None
    numberOfDataSamplesUsed: int | None = None
    trainingTimeDuration: int | None = None
    modelPerformanceOnClient: list[ModelPerformance] | None = None
