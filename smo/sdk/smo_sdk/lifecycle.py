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

    def update_training_job_model_metrics(self, training_job_id: uuid.UUID | str, model_metrics: dict) -> dict:
        # model_metrics is the route's only body-eligible parameter — the
        # JSON body is the metrics dict itself, unwrapped.
        return ensure_ok(self._r1.post(f"/aimgf/training-jobs/{training_job_id}/model-metrics", json=model_metrics))

    def get_training_job_model_metrics(self, training_job_id: uuid.UUID | str) -> dict:
        return ensure_ok(self._r1.get(f"/aimgf/training-jobs/{training_job_id}/model-metrics"))

    def list_training_jobs(self, model_id: uuid.UUID | str | None = None, status: str | None = None) -> list[dict]:
        return ensure_ok(self._r1.get("/aimgf/training-jobs", params={"model_id": model_id, "status": status}))

    # ---------------------------------------------------------------- AIMgF: lifecycle FSM + inference

    def advance_model_lifecycle(self, model_id: uuid.UUID | str, event: str) -> dict:
        return ensure_ok(self._r1.post(f"/aimgf/models/{model_id}/advance", params={"event": event}))

    def request_inference(self, model_id: uuid.UUID | str, notification_destination: str | None = None) -> dict:
        return ensure_ok(self._r1.post(f"/aimgf/models/{model_id}/inference-jobs",
                                        params={"notification_destination": notification_destination}))

    def get_inference_job_status(self, inference_job_id: uuid.UUID | str) -> dict:
        return ensure_ok(self._r1.get(f"/aimgf/inference-jobs/{inference_job_id}/status"))

    def resolve_inference(self, inference_job_id: uuid.UUID | str, succeeded: bool) -> dict:
        return ensure_ok(self._r1.post(f"/aimgf/inference-jobs/{inference_job_id}/resolve", params={"succeeded": succeeded}))

    def list_inference_jobs(self, model_id: uuid.UUID | str | None = None, status: str | None = None) -> list[dict]:
        return ensure_ok(self._r1.get("/aimgf/inference-jobs", params={"model_id": model_id, "status": status}))

    # ---------------------------------------------------------------- AIMgF: MLMF performance monitoring

    def subscribe_performance_monitoring(self, model_id: uuid.UUID | str, metric_types: list[str],
                                          dme_type_id: uuid.UUID | str, guard_kpi_floor: dict | None = None) -> dict:
        return ensure_ok(self._r1.post("/aimgf/mlmf/subscriptions", params={
            "model_id": str(model_id), "dme_type_id": str(dme_type_id),
        }, json={"metric_types": metric_types, "guard_kpi_floor": guard_kpi_floor}))

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

    def list_feature_groups(self) -> dict:
        return ensure_ok(self._r1.get("/aimgf/feature-groups"))

    # ---------------------------------------------------------------- MLLF: deploy

    def deploy_model(self, model_id: uuid.UUID | str, node_groups: list[str]) -> dict:
        # node_groups is the route's only body parameter (a plain list) —
        # unwrapped, not {"node_groups": [...]}.
        return ensure_ok(self._r1.post(f"/mllf/models/{model_id}/deploy", json=node_groups))
