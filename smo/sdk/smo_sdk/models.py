"""sdk.models — a thin client over MLMR (`mlmr/`), TS 28.105's model
repository. Every method verified against mlmr/app/main.py's own live
OpenAPI schema (query params vs body shape) before being written, not
assumed from the route signatures alone.
"""

import uuid

from ._common import BaseClient, ensure_ok


class ModelsClient(BaseClient):
    def register_model(self, model_type: str, version: str, required_resource_type_id: str | None = None,
                        description: str | None = None, author: str | None = None, owner: str | None = None,
                        input_data_type: str | None = None, output_data_type: str | None = None,
                        target_environments: list[dict] | None = None) -> dict:
        return ensure_ok(self._r1.post("/mlmr/models", json={
            "modelType": model_type, "version": version, "requiredResourceTypeId": required_resource_type_id,
            "description": description, "author": author, "owner": owner, "inputDataType": input_data_type,
            "outputDataType": output_data_type, "targetEnvironments": target_environments or [],
        }))

    def discover_models(self, model_type: str | None = None) -> list[dict]:
        return ensure_ok(self._r1.get("/mlmr/models", params={"model_type": model_type}))

    def get_model(self, model_id: uuid.UUID | str) -> dict:
        return ensure_ok(self._r1.get(f"/mlmr/models/{model_id}"))

    def update_model(self, model_id: uuid.UUID | str, model_type: str, version: str, **fields) -> dict:
        """`model_type`/`version` are the model's own immutable identity —
        UpdateModel 400s if they don't match the existing record (see
        mlmr/app/main.py's own docstring). `fields` may include any of
        requiredResourceTypeId/trainingDataLineage/integrityHash/
        clearedNodeGroups/description/author/owner/inputDataType/
        outputDataType/targetEnvironments.
        """
        return ensure_ok(self._r1.put(f"/mlmr/models/{model_id}", json={
            "modelType": model_type, "version": version, **fields,
        }))

    def deregister_model(self, model_id: uuid.UUID | str) -> None:
        ensure_ok(self._r1.delete(f"/mlmr/models/{model_id}"))

    def upload_artifact(self, model_id: uuid.UUID | str, filename: str, content: bytes) -> dict:
        return ensure_ok(self._r1.post(f"/mlmr/models/{model_id}/artifact", files={
            "file": (filename, content, "application/zip"),
        }))

    def download_artifact(self, model_id: uuid.UUID | str, artifact_version: int):
        """Returns the raw response (not JSON) — the artifact's bytes are
        in `.content`, matching download_model_artifact's own
        application/zip response.
        """
        resp = self._r1.get(f"/mlmr/models/{model_id}/artifact/{artifact_version}")
        if resp.status_code >= 400:
            from ._common import SdkError
            raise SdkError(resp.status_code, resp.text)
        return resp

    def create_coordination_group(self, member_model_ids: list[uuid.UUID | str], member_use_cases: list[str] | None = None,
                                   shared_feature_pipeline_ref: str | None = None,
                                   retrain_propagation: str = "ANY_MEMBER_TRIGGERS") -> dict:
        return ensure_ok(self._r1.post("/mlmr/coordination-groups", json={
            "memberModelIds": [str(m) for m in member_model_ids], "memberUseCases": member_use_cases or [],
            "sharedFeaturePipelineRef": shared_feature_pipeline_ref, "retrainPropagation": retrain_propagation,
        }))

    def list_coordination_groups(self) -> list[dict]:
        return ensure_ok(self._r1.get("/mlmr/coordination-groups"))
