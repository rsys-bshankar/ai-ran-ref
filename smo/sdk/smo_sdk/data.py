"""sdk.data — a thin client over DME (`dme/`), R1AP clause 7.

One method per real DME route (dme/app/main.py, read fresh before writing
this); no new business logic, no client-side re-validation of what DME
itself already validates (schema checks, delivery-method commitments).
"""

import uuid

from ._common import BaseClient, ensure_ok


class DataClient(BaseClient):
    def register_type(self, namespace: str, name: str, version: str, type_name: str, producer_id: str,
                       data_production_schema: dict, producer_health_callback_url: str, job_callback_url: str,
                       collection_spec: dict | None = None, source_domain: str | None = None,
                       source_context: dict | None = None) -> dict:
        """`source_domain`/`source_context` (Wave 3, docs/ownership/DME_OWNERSHIP.md):
        LIVE_RAN | DIGITAL_TWIN provenance driving the Digital-Twin-
        excluded-from-inference eligibility rule; both optional.
        """
        return ensure_ok(self._r1.post("/dme/production-capabilities", json={
            "namespace": namespace, "name": name, "version": version, "typeName": type_name,
            "producerId": producer_id, "dataProductionSchema": data_production_schema,
            "collectionSpec": collection_spec, "producerHealthCallbackUrl": producer_health_callback_url,
            "jobCallbackUrl": job_callback_url, "sourceDomain": source_domain, "sourceContext": source_context,
        }))

    def discover_types(self, data_category: str | None = None) -> list[dict]:
        return ensure_ok(self._r1.get("/dme/dme-types", params={"data_category": data_category}))

    def list_producers(self) -> list[dict]:
        """The real ICS `GET /data-producer/v1/info-producers` — a
        producer is now its own real entity, not implied by a DmeType row.
        """
        return ensure_ok(self._r1.get("/dme/production-capabilities"))

    def get_producer(self, producer_id: str) -> dict:
        return ensure_ok(self._r1.get(f"/dme/production-capabilities/{producer_id}"))

    def deregister_producer(self, producer_id: str) -> None:
        """Removes only this producer and its type links — the types it
        supported stay registered (see `delete_type`) if any other
        producer still supports them, or simply go DISABLED if not.
        """
        ensure_ok(self._r1.delete("/dme/production-capabilities", params={"producer_id": producer_id}))

    def delete_type(self, dme_type_id: uuid.UUID | str) -> None:
        """The real ICS `DELETE /data-producer/v1/info-types/{id}` —
        409s if any producer still supports this type.
        """
        ensure_ok(self._r1.delete(f"/dme/dme-types/{dme_type_id}"))

    def query_producer_status(self, producer_id: str) -> dict:
        return ensure_ok(self._r1.get(f"/dme/production-capabilities/{producer_id}/status"))

    def create_data_job(self, dme_type_id: uuid.UUID | str, data_delivery_mode: str, data_delivery_method: str,
                         consumer_id: str, production_job_definition: dict | None = None, delivery_details: dict | None = None,
                         lifecycle_stage: str | None = None) -> dict:
        """`lifecycle_stage` (Wave 3): TRAINING|TESTING|EMULATION|INFERENCE|
        CLOSED_LOOP_FEEDBACK — DME 422s if this job's type is a
        DIGITAL_TWIN source and lifecycle_stage is INFERENCE.
        """
        return ensure_ok(self._r1.post("/dme/data-jobs", json={
            "dataDeliveryMode": data_delivery_mode, "dmeTypeId": str(dme_type_id),
            "productionJobDefinition": production_job_definition or {}, "dataDeliveryMethod": data_delivery_method,
            "deliveryDetails": delivery_details or {}, "consumerId": consumer_id, "lifecycleStage": lifecycle_stage,
        }))

    def get_data_job(self, data_job_id: uuid.UUID | str) -> dict:
        return ensure_ok(self._r1.get(f"/dme/data-jobs/{data_job_id}"))

    def update_data_job(self, data_job_id: uuid.UUID | str, dme_type_id: uuid.UUID | str, data_delivery_mode: str,
                         data_delivery_method: str, consumer_id: str, production_job_definition: dict | None = None,
                         delivery_details: dict | None = None) -> dict:
        return ensure_ok(self._r1.put(f"/dme/data-jobs/{data_job_id}", json={
            "dataDeliveryMode": data_delivery_mode, "dmeTypeId": str(dme_type_id),
            "productionJobDefinition": production_job_definition or {}, "dataDeliveryMethod": data_delivery_method,
            "deliveryDetails": delivery_details or {}, "consumerId": consumer_id,
        }))

    def query_data_job_status(self, data_job_id: uuid.UUID | str) -> dict:
        return ensure_ok(self._r1.get(f"/dme/data-jobs/{data_job_id}/status"))

    def terminate_data_job(self, data_job_id: uuid.UUID | str) -> None:
        ensure_ok(self._r1.delete(f"/dme/data-jobs/{data_job_id}"))

    def terminate_data_jobs_for_consumer(self, consumer_id: str) -> None:
        """The real ICS deleteJobsForOwner (ics-api.yaml) — every job this
        consumer owns, torn down in one call rather than one terminate_data_job
        per id.
        """
        ensure_ok(self._r1.delete("/dme/data-jobs", params={"consumer_id": consumer_id}))

    def list_data_jobs(self, dme_type_id: uuid.UUID | str | None = None, consumer_id: str | None = None) -> list[dict]:
        return ensure_ok(self._r1.get("/dme/data-jobs", params={"dme_type_id": dme_type_id, "consumer_id": consumer_id}))

    def create_data_offer(self, dme_type_id: uuid.UUID | str, data_delivery_mode: str, data_delivery_methods: list[str],
                           data_offer_termination_notification_uri: str, production_job_definition: dict | None = None,
                           data_availability_notification_uri: str | None = None) -> dict:
        return ensure_ok(self._r1.post("/dme/offers", json={
            "dmeTypeId": str(dme_type_id), "dataDeliveryMode": data_delivery_mode,
            "productionJobDefinition": production_job_definition or {}, "dataDeliveryMethods": data_delivery_methods,
            "dataAvailabilityNotificationUri": data_availability_notification_uri,
            "dataOfferTerminationNotificationUri": data_offer_termination_notification_uri,
        }))

    def get_data_offer(self, offer_id: uuid.UUID | str) -> dict:
        return ensure_ok(self._r1.get(f"/dme/offers/{offer_id}"))

    def terminate_data_offer(self, offer_id: uuid.UUID | str) -> None:
        ensure_ok(self._r1.delete(f"/dme/offers/{offer_id}"))

    def list_data_offers(self, dme_type_id: uuid.UUID | str | None = None) -> list[dict]:
        return ensure_ok(self._r1.get("/dme/offers", params={"dme_type_id": dme_type_id}))

    def notify_data_available(self, offer_id: uuid.UUID | str, payload: dict) -> None:
        ensure_ok(self._r1.post(f"/dme/offers/{offer_id}/notify", json=payload))

    def subscribe_type_changes(self, notification_destination: str, owner: str) -> dict:
        return ensure_ok(self._r1.post("/dme/type-subscriptions", json={
            "notificationDestination": notification_destination, "owner": owner,
        }))

    def list_type_subscriptions(self, owner: str | None = None) -> list[dict]:
        return ensure_ok(self._r1.get("/dme/type-subscriptions", params={"owner": owner}))

    def get_type_subscription(self, subscription_id: uuid.UUID | str) -> dict:
        return ensure_ok(self._r1.get(f"/dme/type-subscriptions/{subscription_id}"))

    def unsubscribe_type_changes(self, subscription_id: uuid.UUID | str) -> None:
        ensure_ok(self._r1.delete(f"/dme/type-subscriptions/{subscription_id}"))

    # ---------------------------------------------------------------- Wave 3: real data-plane store
    # docs/ownership/DME_OWNERSHIP.md — a producer ingests, a consumer
    # (rApp or MDAF, no distinction here) fetches. Not restricted to
    # either caller.

    def ingest_data_record(self, data_job_id: uuid.UUID | str, payload: dict) -> dict:
        return ensure_ok(self._r1.post(f"/dme/data-jobs/{data_job_id}/records", json={"payload": payload}))

    def fetch_data_records(self, data_job_id: uuid.UUID | str, limit: int = 100) -> list[dict]:
        return ensure_ok(self._r1.get(f"/dme/data-jobs/{data_job_id}/records", params={"limit": limit}))

    # ---------------------------------------------------------------- Wave 3: O1 action mediation
    # docs/ownership/DME_OWNERSHIP.md — DME mediates and forwards to
    # ran-nf-oam's real NETCONF dispatch; it doesn't speak O1 itself.

    def mediate_action(self, requested_by: str, changes: list[dict], scope: str = "single-ME",
                        msac_role: str | None = None, source_context: dict | None = None) -> dict:
        return ensure_ok(self._r1.post("/dme/actions", json={
            "requestedBy": requested_by, "changes": changes, "scope": scope,
            "msacRole": msac_role, "sourceContext": source_context,
        }))

    def get_action(self, action_id: uuid.UUID | str) -> dict:
        return ensure_ok(self._r1.get(f"/dme/actions/{action_id}"))

    def list_actions(self, managed_element_ref: str | None = None, requested_by: str | None = None) -> list[dict]:
        return ensure_ok(self._r1.get("/dme/actions", params={"managed_element_ref": managed_element_ref, "requested_by": requested_by}))
