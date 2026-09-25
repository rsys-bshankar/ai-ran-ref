"""DME (Data Management and Exposure).

R1AP clause 7, R1AP's own original design — least mature R1 service group
(3 of 6 APIs alpha). Foundational Platform LLD section 3 is authoritative;
this is where the LLD's headline finding gets built: DataJob (section 3.3)
never had a schema OR endpoints in v1.3 at all.
"""

import datetime
import uuid

import httpx
import jsonschema
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error

from .models import DELIVERY_METHODS, DataJob, DataOffer, DMEType, DMETypeSubscription

app = FastAPI(title="DME — Data Management and Exposure")


@app.get("/health")
def health_check():
    """Liveness probe. The GUI BFF's GET /modules/status fans out to
    /<module>/health through R1 Termination for every module in parallel,
    so every module answers one — previously only ran-nf-oam/a1-related
    did (as their own DME producer-health callback URL).
    """
    return {"status": "healthy"}


class DMETypeRegistration(BaseModel):
    namespace: str
    name: str
    version: str
    typeName: str
    producerId: str
    dataProductionSchema: dict
    collectionSpec: dict | None = None
    producerHealthCallbackUrl: str
    jobCallbackUrl: str


class DataJobRequest(BaseModel):
    dataDeliveryMode: str  # ONE_TIME | CONTINUOUS
    dmeTypeId: uuid.UUID
    productionJobDefinition: dict = {}
    dataDeliveryMethod: str  # PULL_HTTP | PUSH_HTTP | STREAMING_KAFKA
    deliveryDetails: dict = {}
    consumerId: str


class DataOfferRequest(BaseModel):
    dmeTypeId: uuid.UUID
    dataDeliveryMode: str
    productionJobDefinition: dict = {}
    dataDeliveryMethods: list[str]
    dataAvailabilityNotificationUri: str | None = None
    dataOfferTerminationNotificationUri: str


class TypeSubscriptionRequest(BaseModel):
    notificationDestination: str
    owner: str


@app.post("/production-capabilities", status_code=201)
def register_dme_type(body: DMETypeRegistration, db: Session = Depends(get_session)):
    t = DMEType(
        namespace=body.namespace,
        name=body.name,
        version=body.version,
        type_name=body.typeName,
        producer_id=body.producerId,
        data_production_schema=body.dataProductionSchema,
        collection_spec=body.collectionSpec,
        producer_health_callback_url=body.producerHealthCallbackUrl,
        job_callback_url=body.jobCallbackUrl,
    )
    db.add(t)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise framework_error(FrameworkError.DME_TYPE_VERSION_CONFLICT, detail=f"{body.namespace}:{body.name}:{body.version} already registered")
    _notify_type_subscribers(db, t.dme_type_id, t.data_production_schema, "REGISTERED")
    return {"registrationId": str(t.dme_type_id)}


@app.get("/dme-types")
def discover_dme_types(data_category: str | None = None, db: Session = Depends(get_session)):
    """OPEN_ITEMS.md section 5: data_category was declared but silently
    never applied to the query. DMEType has no dedicated category
    column — namespace (the grouping half of R1AP's typeName convention,
    e.g. "RAN" in "RAN.CoverageIssue") is the closest concept it does
    have, so that's what this filters on.
    """
    stmt = select(DMEType)
    if data_category:
        stmt = stmt.where(DMEType.namespace == data_category)
    rows = db.scalars(stmt).all()
    return [_type_view(r) for r in rows]


@app.delete("/production-capabilities", status_code=204)
def deregister_producer(producer_id: str, db: Session = Depends(get_session)):
    """The DME half of rApp Management's producer-reconsideration trigger
    (OPEN_ITEMS.md section 1): when a RAppInstance crashes or terminates,
    its own DME registrations (keyed by producer_id == the rApp's
    oauth_client_id) are no longer trustworthy and are torn down here,
    same as terminate_data_job's shape. Idempotent — a producer_id with
    nothing registered is a no-op, not an error.

    OPEN_ITEMS.md section 5: this used to delete each DMEType row
    unconditionally, leaving any DataJob/DataOffer still referencing
    that type either orphaned (no FK enforcement under SQLite) or
    crashing with an unhandled IntegrityError (real Postgres — neither
    FK had an ON DELETE CASCADE, unlike dme_delivery_schema's own
    already-cascading one). A job or offer for a type nobody produces
    anymore is meaningless once the producer is gone, so this cleans
    them up explicitly — the DB-level ON DELETE CASCADE (added
    alongside this) is a defense-in-depth backstop, not the only line
    of defense.
    """
    types = db.scalars(select(DMEType).where(DMEType.producer_id == producer_id)).all()
    removed = [(t.dme_type_id, t.data_production_schema) for t in types]  # snapshot before delete — post-commit access on a deleted row would fail
    for t in types:
        db.query(DataJob).filter(DataJob.dme_type_id == t.dme_type_id).delete()
        db.query(DataOffer).filter(DataOffer.dme_type_id == t.dme_type_id).delete()
        db.delete(t)
    db.commit()
    for dme_type_id, schema in removed:
        _notify_type_subscribers(db, dme_type_id, schema, "DEREGISTERED")


@app.get("/production-capabilities/{producer_id}/status")
def query_producer_status(producer_id: str, db: Session = Depends(get_session)):
    """OPEN_ITEMS.md section 5: no producer-status endpoint existed at
    all. ICS's own GET .../info-producers/{id}/status
    (ProducerController.getInfoProducerStatus) returns a single
    ENABLED/DISABLED operational_state per producer, derived from the
    same producer-availability signal typeStatus itself now uses
    (ProducerStatusInfo, producer.isAvailable()). Our schema has no
    separate InfoProducer entity — a producer is however many DMEType
    rows share its producer_id — so this reuses the first one's
    producer_health_callback_url, live, same as _computed_type_status.
    404 if the producer has nothing registered at all, matching ICS's
    own getProducer-not-found behavior.
    """
    t = db.scalar(select(DMEType).where(DMEType.producer_id == producer_id).limit(1))
    if t is None:
        raise HTTPException(status_code=404, detail="no such producer")
    operational_state = "ENABLED" if _producer_is_healthy(t.producer_health_callback_url) else "DISABLED"
    return {"producerId": producer_id, "operationalState": operational_state}


@app.post("/type-subscriptions", status_code=201)
def subscribe_type_changes(body: TypeSubscriptionRequest, db: Session = Depends(get_session)):
    """OPEN_ITEMS.md section 5: ICS's own `/info-type-subscription`
    (InfoTypeSubscriptions/ConsumerCallbacks) — a consumer notified
    whenever any DmeType is registered or removed. Entirely absent from
    this build until now. ICS's own PUT is create-or-update against a
    caller-supplied subscriptionId; this build's id is server-generated
    (same adaptation already made for every other subscription in this
    codebase — RAN Analytics, A1 Related, Policy Mgmt, FOCOM).
    """
    sub = DMETypeSubscription(notification_destination=body.notificationDestination, owner=body.owner)
    db.add(sub)
    db.commit()
    return {"subscriptionId": str(sub.subscription_id)}


@app.get("/type-subscriptions")
def list_type_subscriptions(owner: str | None = None, db: Session = Depends(get_session)):
    stmt = select(DMETypeSubscription)
    if owner:
        stmt = stmt.where(DMETypeSubscription.owner == owner)
    return [_subscription_view(s) for s in db.scalars(stmt).all()]


@app.get("/type-subscriptions/{subscription_id}")
def get_type_subscription(subscription_id: uuid.UUID, db: Session = Depends(get_session)):
    sub = db.get(DMETypeSubscription, subscription_id)
    if sub is None:
        raise HTTPException(status_code=404, detail="no such subscription")
    return _subscription_view(sub)


@app.delete("/type-subscriptions/{subscription_id}", status_code=204)
def unsubscribe_type_changes(subscription_id: uuid.UUID, db: Session = Depends(get_session)):
    sub = db.get(DMETypeSubscription, subscription_id)
    if sub is not None:
        db.delete(sub)
        db.commit()


def _notify_type_subscribers(db: Session, dme_type_id: uuid.UUID, job_data_schema: dict, status: str) -> None:
    """ICS's own ConsumerCallbacks.notifyTypeRegistered/notifyTypeRemoved
    (InfoTypeSubscriptions) — POSTs to every subscriber's
    notificationDestination whenever any DmeType is registered or
    removed. Unfiltered, matching the reference: ICS's own subscription
    has no per-type scoping at all — every subscriber hears about every
    type change. Best-effort, same pattern as every other subscriber
    notification in this build.
    """
    for sub in db.scalars(select(DMETypeSubscription)).all():
        try:
            httpx.post(sub.notification_destination, json={
                "infoTypeId": str(dme_type_id), "jobDataSchema": job_data_schema, "status": status,
            }, timeout=2.0)
        except httpx.HTTPError:
            pass


def _subscription_view(s: DMETypeSubscription) -> dict:
    return {"subscriptionId": str(s.subscription_id), "notificationDestination": s.notification_destination, "owner": s.owner}


def _validate_delivery_method(db: Session, dme_type_id: uuid.UUID, method: str) -> None:
    if method not in DELIVERY_METHODS:
        raise framework_error(FrameworkError.DELIVERY_METHOD_NOT_OFFERED, detail=f"unknown method {method}")
    # Cross-check against the actual DataOffer(s) for this dmeTypeId, not just
    # the global wire-value set — a consumer requesting a method no offer for
    # this type ever committed to was previously accepted without complaint.
    # A type with no DataOffer at all skips this (not every DmeType requires
    # one in this build), so this only tightens the case where an offer exists.
    offers = db.scalars(select(DataOffer).where(DataOffer.dme_type_id == dme_type_id)).all()
    if offers and not any(o.data_delivery_method_committed == method for o in offers):
        raise framework_error(FrameworkError.DELIVERY_METHOD_NOT_OFFERED, detail=f"{method} not committed by any DataOffer for this dmeTypeId")


def _validate_job_definition_schema(db: Session, dme_type_id: uuid.UUID, definition: dict) -> None:
    """OPEN_ITEMS.md section 5: ICS's own InfoJobs.validateJsonObjectAgainstSchema
    (org.everit.json.schema, called from validatePutInfoJob) — productionJobDefinition
    was accepted as an arbitrary dict, never checked against the DmeType's own
    dataProductionSchema (R1AP's actual contract for what a valid job
    definition looks like). A dmeTypeId with no registered DmeType at all
    skips this, same permissive shape as _validate_delivery_method's own
    offer check — nothing else in create_data_job enforces the type's
    existence either.
    """
    dme_type = db.get(DMEType, dme_type_id)
    if dme_type is None:
        return
    try:
        jsonschema.validate(instance=definition, schema=dme_type.data_production_schema)
    except jsonschema.ValidationError as e:
        raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED, detail=e.message)
    except jsonschema.SchemaError as e:
        # The registered dataProductionSchema itself is malformed — not the
        # caller's fault, but there's no meaningful way to validate against it.
        raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED, detail=f"registered dataProductionSchema is invalid: {e.message}")


@app.post("/data-jobs", status_code=202)
def create_data_job(body: DataJobRequest, db: Session = Depends(get_session)):
    _validate_delivery_method(db, body.dmeTypeId, body.dataDeliveryMethod)
    _validate_job_definition_schema(db, body.dmeTypeId, body.productionJobDefinition)
    job = DataJob(
        data_delivery_mode=body.dataDeliveryMode,
        dme_type_id=body.dmeTypeId,
        production_job_definition=body.productionJobDefinition,
        data_delivery_method=body.dataDeliveryMethod,
        delivery_details=body.deliveryDetails,
        consumer_id=body.consumerId,
        status="ACTIVE",
    )
    db.add(job)
    db.commit()
    dme_type = db.get(DMEType, body.dmeTypeId)
    if dme_type is not None:
        _push_job_to_producer(dme_type, job)
    return {"dataJobId": str(job.data_job_id)}


@app.get("/data-jobs/{data_job_id}")
def get_data_job(data_job_id: uuid.UUID, db: Session = Depends(get_session)):
    job = db.get(DataJob, data_job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such data job")
    return _job_view(job)


@app.put("/data-jobs/{data_job_id}")
def update_data_job(data_job_id: uuid.UUID, body: DataJobRequest, db: Session = Depends(get_session)):
    """PutIndividualInfoJob (ICS's ConsumerController.java) — DME had no
    update-in-place semantics at all, only POST-create/DELETE. ICS's own
    PUT is create-or-update against a caller-supplied jobId (201 new /
    200 updated); this build's data_job_id is always server-generated
    (see create_data_job), so this endpoint only ever updates an
    existing job — 404 on an unknown id, matching this module's other
    GET/DELETE-by-id routes. ICS itself also rejects changing a job's
    type mid-update ("Cannot modify job type", 409 there) — the
    equivalent identity fields here are dmeTypeId/consumerId/
    dataDeliveryMode, all fixed at creation and immutable via this
    endpoint (same adaptation AI/ML Workflow's update_model already
    made for its own identity fields).
    """
    job = db.get(DataJob, data_job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such data job")
    if job.dme_type_id != body.dmeTypeId or job.consumer_id != body.consumerId or job.data_delivery_mode != body.dataDeliveryMode:
        raise framework_error(FrameworkError.DATA_JOB_TARGET_IMMUTABLE, detail="dmeTypeId/consumerId/dataDeliveryMode cannot change on update")
    _validate_delivery_method(db, body.dmeTypeId, body.dataDeliveryMethod)
    _validate_job_definition_schema(db, body.dmeTypeId, body.productionJobDefinition)
    job.production_job_definition = body.productionJobDefinition
    job.data_delivery_method = body.dataDeliveryMethod
    job.delivery_details = body.deliveryDetails
    db.commit()
    dme_type = db.get(DMEType, job.dme_type_id)
    if dme_type is not None:
        # ICS re-runs startInfoSubscriptionJob on every PUT, new or
        # updated — the producer is re-notified with the new job
        # definition, not just on first creation.
        _push_job_to_producer(dme_type, job)
    return _job_view(job)


@app.get("/data-jobs/{data_job_id}/status")
def query_data_job_status(data_job_id: uuid.UUID, db: Session = Depends(get_session)):
    job = db.get(DataJob, data_job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="no such data job")
    return {"dataJobId": str(job.data_job_id), "status": job.status}


@app.delete("/data-jobs/{data_job_id}", status_code=204)
def terminate_data_job(data_job_id: uuid.UUID, db: Session = Depends(get_session)):
    """Handles both directions per section 3.7: a Consumer rApp cancelling
    its own job, or the DME framework itself tearing down a job it created
    against a Producer rApp (consumer_id == 'DME_FRAMEWORK').
    """
    job = db.get(DataJob, data_job_id)
    if job is None:
        return
    dme_type = db.get(DMEType, job.dme_type_id)
    db.delete(job)
    db.commit()
    if dme_type is not None:
        _stop_job_at_producer(dme_type, data_job_id)


@app.post("/offers", status_code=201)
def create_data_offer(body: DataOfferRequest, db: Session = Depends(get_session)):
    if not set(body.dataDeliveryMethods) <= DELIVERY_METHODS:
        raise framework_error(FrameworkError.DELIVERY_METHOD_NOT_OFFERED)
    offer = DataOffer(
        dme_type_id=body.dmeTypeId,
        data_delivery_methods_offered=body.dataDeliveryMethods,
        data_delivery_method_committed=body.dataDeliveryMethods[0],  # framework commits to one, section 3.5
        data_availability_notification_uri=body.dataAvailabilityNotificationUri,
        data_offer_termination_notification_uri=body.dataOfferTerminationNotificationUri,
    )
    db.add(offer)
    db.commit()
    return {"offerId": str(offer.offer_id), "committedMethod": offer.data_delivery_method_committed}


@app.get("/offers/{offer_id}")
def get_data_offer(offer_id: uuid.UUID, db: Session = Depends(get_session)):
    offer = db.get(DataOffer, offer_id)
    if offer is None:
        raise HTTPException(status_code=404, detail="no such data offer")
    return _offer_view(offer)


@app.delete("/offers/{offer_id}", status_code=204)
def terminate_data_offer(offer_id: uuid.UUID, db: Session = Depends(get_session)):
    """Section 3.5: drain in-flight notifications before tearing down —
    Phase 1, 'drain' means simply not accepting new availability
    notifications for this offer once termination starts; a real queue
    drain is future work, flagged rather than silently skipped.
    """
    offer = db.get(DataOffer, offer_id)
    if offer is None:
        return
    termination_uri = offer.data_offer_termination_notification_uri
    db.delete(offer)
    db.commit()
    try:
        httpx.post(termination_uri, json={"dataOfferId": str(offer_id)}, timeout=5.0)  # normal direction
    except httpx.HTTPError:
        pass


@app.post("/offers/{offer_id}/notify", status_code=204)
def offer_data_availability(offer_id: uuid.UUID, body: dict, db: Session = Depends(get_session)):
    """REVERSED direction (section 3.5) — the Producer rApp calls THIS
    endpoint to tell the framework its offered data is ready. Every other
    DME notification flows the opposite way.
    """
    offer = db.get(DataOffer, offer_id)
    if offer is None:
        raise framework_error(FrameworkError.DME_TYPE_VERSION_CONFLICT, detail="no such offer")
    # Phase 1: framework pulls/receives here — deferred to the actual pull/push
    # transport handler (dme-pull/dme-push routes), this endpoint just acks.


def _push_job_to_producer(dme_type: DMEType, job: DataJob) -> None:
    """OPEN_ITEMS.md section 5: no job push to producers existed at
    all — create_data_job/terminate_data_job only ever touched our own
    DB. ICS's own ProducerCallbacks.startInfoJob POSTs the job to the
    producer's jobCallbackUrl (ProducerJobInfo's wire shape); best-effort,
    same pattern as every other DME/FOCOM/A1-Related notification in this
    build — an unreachable producer never fails the consumer-facing call,
    matching the reference's own onErrorResume-and-continue behavior.
    """
    try:
        httpx.post(dme_type.job_callback_url, json={
            "infoJobIdentity": str(job.data_job_id),
            "infoTypeIdentity": str(dme_type.dme_type_id),
            "infoJobData": job.production_job_definition or {},
            "targetUri": (job.delivery_details or {}).get("targetUri", ""),
            "owner": job.consumer_id,
            "lastUpdated": datetime.datetime.now(datetime.UTC).isoformat(),
        }, timeout=5.0)
    except httpx.HTTPError:
        pass


def _stop_job_at_producer(dme_type: DMEType, data_job_id: uuid.UUID) -> None:
    """ICS's own ProducerCallbacks.stopInfoJob — DELETE to
    jobCallbackUrl/{jobId}, best-effort.
    """
    try:
        httpx.delete(f"{dme_type.job_callback_url}/{data_job_id}", timeout=5.0)
    except httpx.HTTPError:
        pass


def _job_view(j: DataJob) -> dict:
    return {
        "dataJobId": str(j.data_job_id),
        "dataDeliveryMode": j.data_delivery_mode,
        "dmeTypeId": str(j.dme_type_id),
        "productionJobDefinition": j.production_job_definition or {},
        "dataDeliveryMethod": j.data_delivery_method,
        "deliveryDetails": j.delivery_details or {},
        "consumerId": j.consumer_id,
        "status": j.status,
    }


def _offer_view(o: DataOffer) -> dict:
    return {
        "offerId": str(o.offer_id),
        "dmeTypeId": str(o.dme_type_id),
        "dataDeliveryMethodsOffered": o.data_delivery_methods_offered,
        "committedMethod": o.data_delivery_method_committed,
        "dataAvailabilityNotificationUri": o.data_availability_notification_uri,
        "dataOfferTerminationNotificationUri": o.data_offer_termination_notification_uri,
    }


def _type_view(t: DMEType) -> dict:
    return {
        "dmeTypeId": str(t.dme_type_id),
        "dmeTypeIdStruct": t.dme_type_id_struct,
        "typeName": t.type_name,
        "producerId": t.producer_id,
        "typeStatus": _computed_type_status(t),  # ADOPT from ICS, section 3.4
        "producerHealthCallbackUrl": t.producer_health_callback_url,
        "jobCallbackUrl": t.job_callback_url,
    }


def _computed_type_status(t: DMEType) -> str:
    """OPEN_ITEMS.md section 5: this used to check only whether a DataJob
    row was ACTIVE — a dead producer with an active job still reported
    ENABLED, and producerHealthCallbackUrl was stored but never actually
    called. ICS's own typeStatus (ConsumerController.typeStatus) is
    driven entirely by real producer availability
    (ProducerSupervision.checkOneProducer's periodic health poll); this
    mirrors that signal — computed live at read time rather than via a
    background scheduler, since no scheduler exists anywhere in this
    build (elided, same as the real PM file-collection pipeline
    elsewhere) — so a producer that's actually unreachable is never
    silently reported ENABLED just because a job happens to be ACTIVE.
    """
    return "ENABLED" if _producer_is_healthy(t.producer_health_callback_url) else "DISABLED"


def _producer_is_healthy(callback_url: str) -> bool:
    try:
        resp = httpx.get(callback_url, timeout=2.0)
        return resp.status_code < 300
    except httpx.HTTPError:
        return False
