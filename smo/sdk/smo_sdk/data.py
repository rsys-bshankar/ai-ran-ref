"""sdk.data — a thin client over DME (`dme/`), R1AP clause 7.

One method per real DME route (dme/app/main.py, read fresh before writing
this); no new business logic, no client-side re-validation of what DME
itself already validates (schema checks, delivery-method commitments).
"""

import uuid

from ._common import BaseClient, SdkError, ensure_ok


class DataClient(BaseClient):
    def register_type(self, namespace: str, name: str, version: str, type_name: str, producer_id: str,
                       data_production_schema: dict, producer_health_callback_url: str, job_callback_url: str,
                       collection_spec: dict | None = None, source_domain: str | None = None,
                       source_context: dict | None = None) -> dict:
        """`source_domain`/`source_context` (Wave 3, docs/ARCHITECTURE.md (DME)):
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
    # docs/ARCHITECTURE.md (DME) — a producer ingests, a consumer
    # (rApp or MDAF, no distinction here) fetches. Not restricted to
    # either caller.

    def ingest_data_record(self, data_job_id: uuid.UUID | str, payload: dict) -> dict:
        return ensure_ok(self._r1.post(f"/dme/data-jobs/{data_job_id}/records", json={"payload": payload}))

    def fetch_data_records(self, data_job_id: uuid.UUID | str, limit: int = 100) -> list[dict]:
        return ensure_ok(self._r1.get(f"/dme/data-jobs/{data_job_id}/records", params={"limit": limit}))

    # ---------------------------------------------------------------- Wave 3: O1 action mediation
    # docs/ARCHITECTURE.md (DME) — DME mediates and forwards to
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

    # ---------------------------------------------------------------- Wave 9: RAN inventory reads (RAN NF OAM)
    # docs/ROADMAP.md W9-01..06 — what an rApp needs to know
    # before deciding an O1 action: a cell's guard attributes (D-5), and the
    # managed element's vendor capabilities.

    def query_cell_guards(self, managed_element_ref: str | None = None, cell_id: str | None = None,
                          cell_class: str | None = None, sector_group: str | None = None,
                          incident_zone: str | None = None) -> list[dict]:
        # unset filters are left out: httpx would send None as an empty
        # string, which the enum-typed cell_class filter rejects
        params = {"managed_element_ref": managed_element_ref, "cell_id": cell_id, "cell_class": cell_class,
                  "sector_group": sector_group, "incident_zone": incident_zone}
        return ensure_ok(self._r1.get("/ran-nf-oam/cell-guards", params={k: v for k, v in params.items() if v is not None}))

    def get_managed_entity(self, managed_element_ref: str) -> dict:
        return ensure_ok(self._r1.get(f"/ran-nf-oam/managed-entities/{managed_element_ref}"))

    def get_vendor_capability(self, vendor_name: str) -> dict:
        return ensure_ok(self._r1.get(f"/ran-nf-oam/vendor-capabilities/{vendor_name}"))

    def get_o1_capabilities(self) -> dict:
        return ensure_ok(self._r1.get("/ran-nf-oam/capabilities"))

    # ---------------------------------------------------------------- Wave 10.1: dataset access and O1 read-back
    # W10-03 (decision D-4): convenience calls named as in the Wave 10
    # documents, over the existing DME / RAN NF OAM routes.

    def get_dataset(self, name: str, consumer_id: str, namespace: str | None = None, lifecycle_stage: str | None = None,
                    max_records: int = 2000) -> dict:
        """A dataset is a DME type, found by its name, type name or leaf name
        (RAN NF OAM's PM counter types are `PMCounters.<counter>`, so
        "PRB_UTILIZATION" finds `PMCounters.PRB_UTILIZATION`). Reuse (or create)
        this consumer's data job on it, and return its records, oldest
        first: {dmeTypeId, dataJobId, sourceDomain, records}. Raises
        SdkError(404) if no such type is registered."""
        dme_type = next((t for t in self.discover_types(namespace)
                         if name in (t["dmeTypeIdStruct"]["name"], t["typeName"])
                         or t["dmeTypeIdStruct"]["name"].rsplit(".", 1)[-1] == name), None)
        if dme_type is None:
            raise SdkError(404, {"title": "DME_TYPE_NOT_FOUND", "detail": f"no dataset {name!r}"})
        type_id = dme_type["dmeTypeId"]
        job = next((j for j in self.list_data_jobs(dme_type_id=type_id, consumer_id=consumer_id)
                    if j.get("lifecycleStage") == lifecycle_stage), None)
        job_id = job["dataJobId"] if job else self.create_data_job(
            type_id, "CONTINUOUS", "PULL_HTTP", consumer_id, lifecycle_stage=lifecycle_stage)["dataJobId"]
        records, offset = [], 0
        while len(records) < max_records:
            page = ensure_ok(self._r1.get(f"/dme/data-jobs/{job_id}/records", params={"limit": 500, "offset": offset}))
            records.extend(page)
            offset += len(page)
            if len(page) < 500:
                break
        return {"dmeTypeId": type_id, "dataJobId": job_id, "sourceDomain": dme_type.get("sourceDomain"),
                "records": list(reversed(records[:max_records]))}

    def read_config(self, managed_element_ref: str, managed_function_ref: str | None = None) -> dict:
        """The managed object's live running configuration (NETCONF
        get-config through RAN NF OAM) — read-after-write verification."""
        return ensure_ok(self._r1.get(f"/ran-nf-oam/managed-entities/{managed_element_ref}/config",
                                      params={"managed_function_ref": managed_function_ref}))
