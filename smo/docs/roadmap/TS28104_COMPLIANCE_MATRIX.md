# TS 28.104 compliance matrix — MDAF

**Wave 5** (`docs/roadmap/WAVES_4_TO_10_WORK_ITEMS.md`, W5-01..W5-05, decision D-9). Generated from
`specs/5G_APIs/TS28104_MdaNrm.yaml` and `TS28104_MdaReport.yaml`, covering the three IOCs and
every report datatype (48 rows). The generator also checks that each attribute and datatype
appears by name in the implementing code.

## Summary

| | |
|---|---|
| IOCs | MDAFunction, MDARequest and MDAReport, all REST resources (`mdaf/app/mda.py`) |
| Rows | 48 |
| Compliant | 48 |
| Recorded deviations | Addressing (id references, no DN tree); the `STREAMING` reportingMethod transport |

**Request-driven, on top of producer-push.** The original producer-push surface
(`POST /reports`, `/subscriptions`, ThresholdInfo conditional notification) is unchanged. On top
of it:

1. An **MDARequest** declares the outputs it wants, with per-IE `filterValue`, edge-triggered
   `threshold` (hysteresis) and `timeOut` filters, for a scope and a `startTime`/`stopTime`
   window.
2. Every report is matched against all open requests, whether it is spec-shaped (`POST /mda-reports`) or legacy (`POST /reports`).
3. Each matching request gets the report delivered per its `reportingMethod`.

Reporting methods:
- `NOTIFICATION`: a best-effort POST to `reportingTarget`.
- `FILE`: the report is downloadable from `GET /mda-reports/{id}/file`, and a
  `notifyFileReady` goes to `reportingTarget`.
- `STREAMING`: recorded and retrievable, but not streamed. This build has no TS 28.532
  `StreamingDataMnS` transport (the same gap SPEC_AUDIT.md records for RAN NF OAM item 3).

**Typed outputs.** `mDAOutputList` is a oneOf in the spec. Here it is validated against the one
output type its own `mDAType` selects; 9 MDATypes have typed outputs, such as PMDataOutput,
CoverageProblemAnalysisOutput and FailurePredictionOutput. Every other type uses MDAOutputEntry
pairs, which the spec allows for all types.

**W5-02: report kinds.** Each report is typed `ANALYTICS`, `PREDICTION` or `DRIFT`. This is
inferred, so PREDICTIONS_PM_DATA, failure-prediction and congestion-prediction types count as
PREDICTION, or set explicitly with `reportKind`. Filter with `GET /mda-reports?report_kind=`.

**W5-03: traffic trend.** The traffic-trend / TRAFFIC_FORECAST report the roadmap names is a
spec PREDICTIONS_PM_DATA report: `PMDataOutput.pmPredictions` holds, for example,
`RRU.PrbUsedDl` per cell. `sdk.analytics.get_prediction(cell, pm_name)` reads the latest one,
and `create_mda_request(...)` subscribes to them.

**W5-04: drift → retrain.** A `DRIFT` report whose outputs name `mLModelRef` is forwarded, through
R1, to that model's AIMgF MLMF subscriptions as a performance report. The existing guard-KPI
floor, and the coordination-group propagation behind it, then decide whether retraining fires.

## Matrix

### MDAFunction

Resource: `/mdaf/mda-functions`

| Attribute / child | Status | Note |
|---|---|---|
| `supportedMDACapabilities` | Compliant |  |
| `supportedMDADomain` | Compliant |  |
| `mLModelRefList` | Compliant | Set at create/replace (readOnly in the spec: this build has no auto-discovery of the backing models) |
| `aIMLInferenceFunctionRefList` | Compliant | As mLModelRefList |
| `MDARequest` | Compliant (containment as reference) | Containment: `mDAFunctionRef` on the request (capabilities enforced) |
| `MDAReport` | Compliant (containment as reference) | Containment: `mDAFunctionRef` on the report |

### MDARequest

Resource: `/mdaf/mda-requests`

| Attribute / child | Status | Note |
|---|---|---|
| `requestedMDAOutputs` | Compliant | Matched per mDAType; mDAOutputIEFilters filterValue / threshold (edge-triggered, hysteresis) / timeOut enforced |
| `reportingMethod` | Compliant | NOTIFICATION → POST to reportingTarget; FILE → `GET /mda-reports/{id}/file` + notifyFileReady; STREAMING → recorded + retrievable (no TS 28.532 streaming transport — see Deviations) |
| `reportingTarget` | Compliant |  |
| `analyticsScope` | Compliant | managedEntitiesScope matched against the report scope; areaScope stored |
| `startTime` | Compliant | Enforced: reports before startTime are not delivered |
| `stopTime` | Compliant | Enforced |
| `recommendationFilter` | Compliant | Stored and returned |
| `performanceThresholdInfo` | Compliant | Stored and returned (TS 28.623 ThresholdInfo) |
| `analysisRequirements` | Compliant | Stored and returned |
| `thresholdMonitorRefList` | Compliant | Stored and returned (TS 28.623 ThresholdMonitor ids) |

### MDAReport

Resource: `/mdaf/mda-reports (+ GET …/{id}/file)`

| Attribute / child | Status | Note |
|---|---|---|
| `mDAReportID` | Compliant | = report id |
| `mDAOutputs` | Compliant | Typed per mDAType (9 typed outputs) or MDAOutputEntry pairs; validated |
| `mDARequestRef` | Compliant | Set when the producer answers one request; `deliveredToRequestRefList` lists every request it satisfied |

### MDA report output datatypes

| Datatype | Status | Note |
|---|---|---|
| `ProjectionDuration` | Compliant | validated by `ts28104.ProjectionDuration` |
| `PagingOptimizationAnalysisOutput` | Compliant | validated by `ts28104.PagingOptimizationAnalysisOutput` (selected by mDAType) |
| `MDAOutputs` | Compliant | validated by `ts28104.MDAOutputs` |
| `MDAOutputEntry` | Compliant | validated by `ts28104.MDAOutputEntry` |
| `Recommended3GPPAction` | Compliant | validated by `ts28104.Recommended3GPPAction` |
| `RecommendedAction` | Compliant | validated by `ts28104.RecommendedAction` |
| `MobilityPerformanceAnalysisOutput` | Compliant | validated by `ts28104.MobilityPerformanceAnalysisOutput` (selected by mDAType) |
| `CoverageProblemAnalysisOutput` | Compliant | validated by `ts28104.CoverageProblemAnalysisOutput` (selected by mDAType) |
| `RadioEnvironmentMap` | Compliant | validated by `ts28104.RadioEnvironmentMap` |
| `CoverageCharacterization` | Compliant | validated by `ts28104.CoverageCharacterization` |
| `TrainingDataAnalysisOutput` | Compliant | validated by `ts28104.TrainingDataAnalysisOutput` (selected by mDAType) |
| `MeasurementDataCorrelationRecommendation` | Compliant | validated by `ts28104.MeasurementDataCorrelationRecommendation` |
| `NFScalingDimensioningDataAnalysisOutput` | Compliant | validated by `ts28104.NFScalingDimensioningDataAnalysisOutput` (selected by mDAType) |
| `PMDataOutput` | Compliant | validated by `ts28104.PMDataOutput` (selected by mDAType) |
| `PmPrediction` | Compliant | validated by `ts28104.PmPrediction` |
| `ThresholdAssessment` | Compliant | validated by `ts28104.ThresholdAssessment` |
| `FailurePredictionOutput` | Compliant | validated by `ts28104.FailurePredictionOutput` (selected by mDAType) |
| `ManagementDataCollectionInfo` | Compliant | validated by `ts28104.ManagementDataCollectionInfo` |
| `TrafficCongestionProblemAnalysisOutput` | Compliant | validated by `ts28104.TrafficCongestionProblemAnalysisOutput` (selected by mDAType) |
| `RETTPAnalyticsAnalysisOutput` | Compliant | validated by `ts28104.RETTPAnalyticsAnalysisOutput` (selected by mDAType) |
| `MDAType` | Compliant | closed enum / structure enforced |
| `ReportingMethod` | Compliant | closed enum / structure enforced |
| `MDADomain` | Compliant | closed enum / structure enforced |
| `ThresholdInfo` | Compliant | closed enum / structure enforced |
| `AnalyticsScopeType` | Compliant | closed enum / structure enforced |
| `MDAOutputPerMDAType` | Compliant | closed enum / structure enforced |
| `MDAOutputIEFilter` | Compliant | closed enum / structure enforced |
| `AnalyticsSchedule` | Compliant | closed enum / structure enforced |
| `AnalysisRequirement` | Compliant | closed enum / structure enforced |
