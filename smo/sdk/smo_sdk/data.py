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
                       collection_spec: dict | None = None) -> dict:
        return ensure_ok(self._r1.post("/dme/production-capabilities", json={
            "namespace": namespace, "name": name, "version": version, "typeName": type_name,
            "producerId": producer_id, "dataProductionSchema": data_production_schema,
            "collectionSpec": collection_spec, "producerHealthCallbackUrl": producer_health_callback_url,
            "jobCallbackUrl": job_callback_url,
        }))

    def discover_types(self, data_category: str | None = None) -> list[dict]:
        return ensure_ok(self._r1.get("/dme/dme-types", params={"data_category": data_category}))

    def deregister_producer(self, producer_id: str) -> None:
        ensure_ok(self._r1.delete("/dme/production-capabilities", params={"producer_id": producer_id}))

    def query_producer_status(self, producer_id: str) -> dict:
        return ensure_ok(self._r1.get(f"/dme/production-capabilities/{producer_id}/status"))

    def create_data_job(self, dme_type_id: uuid.UUID | str, data_delivery_mode: str, data_delivery_method: str,
                         consumer_id: str, production_job_definition: dict | None = None, delivery_details: dict | None = None) -> dict:
        return ensure_ok(self._r1.post("/dme/data-jobs", json={
            "dataDeliveryMode": data_delivery_mode, "dmeTypeId": str(dme_type_id),
            "productionJobDefinition": production_job_definition or {}, "dataDeliveryMethod": data_delivery_method,
            "deliveryDetails": delivery_details or {}, "consumerId": consumer_id,
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
