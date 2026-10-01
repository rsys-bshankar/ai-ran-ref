"""sdk.lifecycle — a thin client over AIMgF (`aimgf/`) and MLLF
(`mllf/`): training, certification, inference, MLMF monitoring, and
deployment. One namespace for both services since an rApp author thinks
of "advance a model through its lifecycle to deployment" as one journey,
even though AIMgF and MLLF are separate backend services (see
docs/architecture/SERVICE_OWNERSHIP_MATRIX.md).

Every method verified against aimgf/app/main.py's and mllf/app/main.py's
own live OpenAPI schemas (query params vs body shape) before being
written.
"""

import uuid

from ._common import BaseClient, ensure_ok


class LifecycleClient(BaseClient):
    # ---------------------------------------------------------------- AIMgF: training

    def request_training(self, producer_id: str, model_id: uuid.UUID | str | None = None,
                          model_coordination_group_id: uuid.UUID | str | None = None, required_data: dict | None = None,
                          validation_criteria: dict | None = None, notification_uri: str | None = None,
                          run_id: str | None = None, training_dataset: str | None = None,
                          validation_dataset: str | None = None, consumer_rapp_id: str | None = None,
                          producer_rapp_id: str | None = None) -> dict:
        return ensure_ok(self._r1.post("/aimgf/training-jobs", json={
            "modelId": str(model_id) if model_id else None,
            "modelCoordinationGroupId": str(model_coordination_group_id) if model_coordination_group_id else None,
            "producerId": producer_id, "requiredData": required_data or {}, "validationCriteria": validation_criteria or {},
            "notificationUri": notification_uri, "runId": run_id, "trainingDataset": training_dataset,
            "validationDataset": validation_dataset, "consumerRappId": consumer_rapp_id, "producerRappId": producer_rapp_id,
        }))

    def get_training_job_status(self, training_job_id: uuid.UUID | str) -> dict:
        return ensure_ok(self._r1.get(f"/aimgf/training-jobs/{training_job_id}/status"))

    def cancel_training(self, training_job_id: uuid.UUID | str) -> None:
        ensure_ok(self._r1.delete(f"/aimgf/training-jobs/{training_job_id}"))

    def suspend_training(self, training_job_id: uuid.UUID | str) -> dict:
        """Wave 3, SPEC_AUDIT.md's AI/ML Workflow section item 6 — only
        legal against an IN_PROGRESS job; 409s otherwise.
        """
        return ensure_ok(self._r1.post(f"/aimgf/training-jobs/{training_job_id}/suspend"))

    def resume_training(self, training_job_id: uuid.UUID | str) -> dict:
        return ensure_ok(self._r1.post(f"/aimgf/training-jobs/{training_job_id}/resume"))

    def update_training_job_model_metrics(self, training_job_id: uuid.UUID | str, model_metrics: dict) -> dict:
        # model_metrics is the route's only body-eligible parameter — the
        # JSON body is the metrics dict itself, unwrapped.
        return ensure_ok(self._r1.post(f"/aimgf/training-jobs/{training_job_id}/model-metrics", json=model_metrics))

    def get_training_job_model_metrics(self, training_job_id: uuid.UUID | str) -> dict:
        return ensure_ok(self._r1.get(f"/aimgf/training-jobs/{training_job_id}/model-metrics"))

    def list_training_jobs(self, model_id: uuid.UUID | str | None = None, status: str | None = None) -> list[dict]:
        return ensure_ok(self._r1.get("/aimgf/training-jobs", params={"model_id": model_id, "status": status}))

    # ---------------------------------------------------------------- AIMgF: lifecycle FSM + inference

    def advance_model_lifecycle(self, model_id: uuid.UUID | str, event: str,
                                 decided_by: str | None = None, rationale: str | None = None) -> dict:
        """Fires any ModelLifecycle transition (Wave 2, `aimgf/app/statemachine.py`)
        — `decided_by` is required by AIMgF itself for the six governance
        decisions (SUBMIT_FOR_APPROVAL/APPROVE/REJECT/CERTIFY/PROMOTE/
        ROLLBACK); omitted here it 422s the same way a direct call would.
        """
        return ensure_ok(self._r1.post(f"/aimgf/models/{model_id}/advance",
                                        params={"event": event, "decided_by": decided_by, "rationale": rationale}))

    def request_inference(self, model_id: uuid.UUID | str, notification_destination: str | None = None) -> dict:
        return ensure_ok(self._r1.post(f"/aimgf/models/{model_id}/inference-jobs",
                                        params={"notification_destination": notification_destination}))

    def get_inference_job_status(self, inference_job_id: uuid.UUID | str) -> dict:
        return ensure_ok(self._r1.get(f"/aimgf/inference-jobs/{inference_job_id}/status"))

    def resolve_inference(self, inference_job_id: uuid.UUID | str, succeeded: bool,
                          inference_outputs: list[dict] | None = None, potential_impact_info: str | None = None) -> dict:
        """Wave 10.1: `inference_outputs` (TS 28.105 InferenceOutput, its
        `outputResult` carrying the rApp's own result) becomes the job's
        AIMLInferenceReport."""
        body = None
        if inference_outputs is not None or potential_impact_info is not None:
            body = {"inferenceOutputs": inference_outputs or [], "potentialImpactInfo": potential_impact_info}
        return ensure_ok(self._r1.post(f"/aimgf/inference-jobs/{inference_job_id}/resolve",
                                       params={"succeeded": succeeded}, json=body))

    def get_inference_report(self, report_id: uuid.UUID | str) -> dict:
        return ensure_ok(self._r1.get(f"/aimgf/aiml-inference-reports/{report_id}"))

    def list_inference_jobs(self, model_id: uuid.UUID | str | None = None, status: str | None = None) -> list[dict]:
        return ensure_ok(self._r1.get("/aimgf/inference-jobs", params={"model_id": model_id, "status": status}))

    # ---------------------------------------------------------------- AIMgF: MLMF performance monitoring

    def subscribe_performance_monitoring(self, model_id: uuid.UUID | str, metric_types: list[str],
                                          dme_type_id: uuid.UUID | str, guard_kpi_floor: dict | None = None,
                                          notification_destination: str | None = None) -> dict:
        """`notification_destination` (SPEC_AUDIT.md's `MLMFSubscription`
        finding, closed): optional, matching every other subscription-
        shaped resource's own permissive shape.
        """
        return ensure_ok(self._r1.post("/aimgf/mlmf/subscriptions", params={
            "model_id": str(model_id), "dme_type_id": str(dme_type_id), "notification_destination": notification_destination,
        }, json={"metric_types": metric_types, "guard_kpi_floor": guard_kpi_floor}))

    def unsubscribe_performance_monitoring(self, subscription_id: uuid.UUID | str) -> None:
        ensure_ok(self._r1.delete(f"/aimgf/mlmf/subscriptions/{subscription_id}"))

    def report_performance(self, subscription_id: uuid.UUID | str, metrics: dict) -> dict:
        # metrics is the route's only body-eligible parameter — unwrapped.
        return ensure_ok(self._r1.post(f"/aimgf/mlmf/subscriptions/{subscription_id}/reports", json=metrics))

    def list_performance_subscriptions(self, model_id: uuid.UUID | str | None = None) -> list[dict]:
        return ensure_ok(self._r1.get("/aimgf/mlmf/subscriptions", params={"model_id": model_id}))

    def list_performance_reports(self, subscription_id: uuid.UUID | str, limit: int = 100) -> list[dict]:
        return ensure_ok(self._r1.get(f"/aimgf/mlmf/subscriptions/{subscription_id}/reports", params={"limit": limit}))

    def list_recent_performance_reports(self, breached_only: bool = False, limit: int = 50) -> list[dict]:
        return ensure_ok(self._r1.get("/aimgf/mlmf/reports", params={"breached_only": breached_only, "limit": limit}))

    # ---------------------------------------------------------------- AIMgF: feature groups

    def create_feature_group(self, feature_group_name: str, feature_list: str, datalake_source: str, host: str,
                              port: str, bucket: str, token: str, db_org: str, measurement: str,
                              enable_dme: bool = False, measured_obj_class: str | None = None,
                              dme_port: str | None = None, source_name: str | None = None) -> dict:
        return ensure_ok(self._r1.post("/aimgf/feature-groups", json={
            "featureGroupName": feature_group_name, "featureList": feature_list, "datalakeSource": datalake_source,
            "host": host, "port": port, "bucket": bucket, "token": token, "dbOrg": db_org, "measurement": measurement,
            "enableDme": enable_dme, "measuredObjClass": measured_obj_class, "dmePort": dme_port, "sourceName": source_name,
        }))

    def list_feature_groups(self) -> list[dict]:
        return ensure_ok(self._r1.get("/aimgf/feature-groups"))

    # ---------------------------------------------------------------- MLLF: deploy

    def deploy_model(self, model_id: uuid.UUID | str, node_groups: list[str]) -> dict:
        # node_groups is the route's only body parameter (a plain list) —
        # unwrapped, not {"node_groups": [...]}.
        return ensure_ok(self._r1.post(f"/mllf/models/{model_id}/deploy", json=node_groups))

    # ---------------------------------------------------------------- Wave 10.1: the full execution-mode lifecycle
    # WAVES_4_TO_10_WORK_ITEMS.md W10-03 (decision D-4): convenience calls
    # named as in the Wave 10 documents, over the existing AIMgF routes.

    def start_training(self, model_id: uuid.UUID | str, producer_id: str, package_id: uuid.UUID | str | None = None,
                       dme_data_job_ids: list[uuid.UUID | str] | None = None, runtime_profile: dict | None = None,
                       timeout_seconds: int | None = None, notification_uri: str | None = None,
                       required_data: dict | None = None, validation_criteria: dict | None = None) -> dict:
        """Starts a training run on an MLTF runtime sized from the package's
        TRAINING runtime profile (or `runtime_profile`)."""
        return ensure_ok(self._r1.post("/aimgf/training-jobs", json=_drop_none({
            "modelId": str(model_id), "producerId": producer_id, "packageId": _str(package_id),
            "dmeDataJobIds": [str(j) for j in dme_data_job_ids] if dme_data_job_ids else None,
            "runtimeProfile": runtime_profile, "timeoutSeconds": timeout_seconds, "notificationUri": notification_uri,
            "requiredData": required_data, "validationCriteria": validation_criteria,
        })))

    def complete_training(self, training_job_id: uuid.UUID | str, succeeded: bool, metrics: dict | None = None,
                          **ts28105_fields) -> dict:
        return self._complete("training-jobs", training_job_id, succeeded, metrics, ts28105_fields)

    def start_validation(self, model_id: uuid.UUID | str, producer_id: str, package_id: uuid.UUID | str | None = None,
                         validation_criteria: dict | None = None, training_job_id: uuid.UUID | str | None = None,
                         timeout_seconds: int | None = None) -> dict:
        return ensure_ok(self._r1.post("/aimgf/validation-jobs", json=_drop_none({
            "modelId": str(model_id), "producerId": producer_id, "packageId": _str(package_id),
            "validationCriteria": validation_criteria, "trainingJobId": _str(training_job_id), "timeoutSeconds": timeout_seconds,
        })))

    def complete_validation(self, validation_job_id: uuid.UUID | str, succeeded: bool, metrics: dict | None = None,
                            **ts28105_fields) -> dict:
        return self._complete("validation-jobs", validation_job_id, succeeded, metrics, ts28105_fields)

    def start_emulation(self, model_id: uuid.UUID | str, producer_id: str, package_id: uuid.UUID | str | None = None,
                        emulation_criteria: dict | None = None, timeout_seconds: int | None = None) -> dict:
        return ensure_ok(self._r1.post("/aimgf/emulation-jobs", json=_drop_none({
            "modelId": str(model_id), "producerId": producer_id, "packageId": _str(package_id),
            "emulationCriteria": emulation_criteria, "timeoutSeconds": timeout_seconds,
        })))

    def complete_emulation(self, emulation_job_id: uuid.UUID | str, succeeded: bool, metrics: dict | None = None,
                           **ts28105_fields) -> dict:
        return self._complete("emulation-jobs", emulation_job_id, succeeded, metrics, ts28105_fields)

    def _complete(self, kind: str, job_id, succeeded: bool, metrics: dict | None, fields: dict) -> dict:
        return ensure_ok(self._r1.post(f"/aimgf/{kind}/{job_id}/complete",
                                       json={"succeeded": succeeded, "metrics": metrics or {}, **fields}))

    def get_model_lifecycle(self, model_id: uuid.UUID | str) -> dict:
        return ensure_ok(self._r1.get(f"/aimgf/models/{model_id}/lifecycle"))

    def deploy_runtime(self, model_id: uuid.UUID | str, package_id: uuid.UUID | str | None = None,
                       runtime_profile: dict | None = None) -> dict:
        """MLIF: instantiates the inference runtime through NFO (needs CERTIFIED/PROMOTED)."""
        return ensure_ok(self._r1.post(f"/aimgf/models/{model_id}/runtime/deploy",
                                       params=_drop_none({"package_id": _str(package_id)}), json=runtime_profile))

    def activate_runtime(self, model_id: uuid.UUID | str) -> dict:
        return ensure_ok(self._r1.post(f"/aimgf/models/{model_id}/runtime/activate"))


def _str(value) -> str | None:
    return str(value) if value is not None else None


def _drop_none(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}
