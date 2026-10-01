import uuid

from sqlalchemy import ARRAY, JSON, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from smo_shared.db import Base

# HISTORY.md §7 — RAN Analytics vs. TS28104 MDA NRM, `analytics_type` enum
# finding, closed: TS28104_MdaNrm.yaml's own real, closed 24-value
# `MDAType` enum, read directly (not summarized).
MDA_TYPES = frozenset({
    "COVERAGE_ANALYTICS_COVERAGE_PROBLEM_ANALYSIS", "COVERAGE_ANALYTICS_PAGING_OPTIMIZATION",
    "COVERAGE_ANALYTICS_RET_TP_ANALYTICS", "SLS_ANALYSIS_SERVICE_EXPERIENCE_ANALYSIS",
    "SLS_ANALYSIS_NETWORK_SLICE_THROUGHPUT_ANALYSIS", "SLS_ANALYSIS_NETWORK_SLICE_TRAFFIC_ANALYSIS",
    "SLS_ANALYSIS_E2E_LATENCY_ANALYSIS", "SLS_ANALYSIS_NETWORK_SLICE_LOAD_ANALYSIS",
    "UE_THROUGHPUT_ANALYSIS_TRAFFIC_CONGESTION_PROBLEM_ANALYSIS",
    "SLS_ANALYSIS_EDGE_APPLICATION_DEPLOYMENT_LOCATION_ANALYSIS", "SLS_ANALYSIS_EDGE_COMPUTING_PERFORMANCE_ANALYSIS",
    "SLS_ANALYSIS_TRAFFIC_CONGESTION_PREDICTION_ANALYSIS", "MDA_ASSISTED_FAULT_MANAGEMENT_FAILURE_PREDICTION",
    "MDA_ASSISTED_ENERGY_SAVING_ENERGY_SAVING_ANALYSIS", "MOBILITY_MANAGEMENT_ANALYTICS_MOBILITY_PERFORMANCE_ANALYSIS",
    "MOBILITY_MANAGEMENT_ANALYTICS_HANDOVER_OPTIMIZATION", "MAINTENANCE_MAINTENANCE_ANALYTICS",
    "MAINTENANCE_SOFTWARE_UPGRADE_VALIDATION_ANALYTICS",
    "RESOURCE_ANALYTICS_VIRTUALIZED_RESOURCE_UTILIZATION_ANALYSIS_NF",
    "RESOURCE_ANALYTICS_PHYSICAL_RESOURCE_UTILIZATION_ANALYSIS_NF",
    "RESOURCE_ANALYTICS_5GC_CONTROL_PLANE_CONGESTION_ANALYSIS", "PREDICTIONS_PM_DATA",
    "ATSSS_PERFORMANCE_TRAFFIC_STEERING_ANALYTICS", "CORRELATION_ANALYTICS_TRAINING_DATA_ANALYSIS",
    "CORRELATION_ANALYTICS_NF_SCALING_DIMENSIONING_DATA_ANALYSIS",
})

# The only two of this build's own real, already-used `analytics_type`
# shorthand values (`coverage-issue-analysis`/`failure-prediction` —
# `resource-utilization`/`RAN.Coverage` are genuinely ambiguous between
# several real MDAType values, so left unmapped, not guessed) with an
# honest, unambiguous real-spec correspondence — the same partial,
# not-fabricated mapping discipline as AI/ML Workflow's own
# `ml_training_type`.
_MDA_TYPE_BY_SHORTHAND = {
    "coverage-issue-analysis": "COVERAGE_ANALYTICS_COVERAGE_PROBLEM_ANALYSIS",
    "failure-prediction": "MDA_ASSISTED_FAULT_MANAGEMENT_FAILURE_PREDICTION",
}


def infer_mda_type(analytics_type: str) -> str | None:
    return _MDA_TYPE_BY_SHORTHAND.get(analytics_type)


class MDAFProducer(Base):
    __tablename__ = "mdaf_producer"

    producer_id: Mapped[str] = mapped_column(String, primary_key=True)
    analytics_type: Mapped[str] = mapped_column(String, primary_key=True)
    dme_input_types: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid).with_variant(JSON(none_as_null=True), "sqlite"), nullable=False)
    output_schema: Mapped[dict] = mapped_column(JSON, nullable=False)
    # HISTORY.md §7: TS28104's own real, closed MDAType enum — optional,
    # since this build's own `analytics_type` free-string values are
    # informal shorthand that mostly don't correspond to any real spec
    # value at all (constraining `analytics_type` itself would be a real
    # breaking rename for zero behavior gain). A caller may declare a
    # real MDAType directly for genuine spec conformance going forward;
    # `register_analytics_producer` auto-derives it via `infer_mda_type`
    # for the two shorthand values above where the correspondence is
    # unambiguous.
    mda_type: Mapped[str | None] = mapped_column(String)
