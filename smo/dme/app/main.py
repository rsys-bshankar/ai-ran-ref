"""DME (Data Management and Exposure).

R1AP clause 7, R1AP's own original design — least mature R1 service group
(3 of 6 APIs alpha). Foundational Platform LLD section 3 is authoritative;
this is where the LLD's headline finding gets built: DataJob (section 3.3)
never had a schema OR endpoints in v1.3 at all.
"""

import uuid

import httpx
from fastapi import Depends, FastAPI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error

from .models import DELIVERY_METHODS, DataJob, DataOffer, DMEType

app = FastAPI(title="DME — Data Management and Exposure")


class DMETypeRegistration(BaseModel):
    namespace: str
    name: str
    version: str
    typeName: str
    producerId: str
    dataProductionSchema: dict
    collectionSpec: dict | None = None
    producerHealthCallbackUrl: str


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
    )
    db.add(t)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise framework_error(FrameworkError.DME_TYPE_VERSION_CONFLICT, detail=f"{body.namespace}:{body.name}:{body.version} already registered")
    return {"registrationId": str(t.dme_type_id)}


@app.get("/dme-types")
def discover_dme_types(data_category: str | None = None, db: Session = Depends(get_session)):
    rows = db.scalars(select(DMEType)).all()
    return [_type_view(db, r) for r in rows]


@app.post("/data-jobs", status_code=202)
def create_data_job(body: DataJobRequest, db: Session = Depends(get_session)):
    if body.dataDeliveryMethod not in DELIVERY_METHODS:
        raise framework_error(FrameworkError.DELIVERY_METHOD_NOT_OFFERED, detail=f"unknown method {body.dataDeliveryMethod}")
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
    return {"dataJobId": str(job.data_job_id)}


@app.delete("/data-jobs/{data_job_id}", status_code=204)
def terminate_data_job(data_job_id: uuid.UUID, db: Session = Depends(get_session)):
    """Handles both directions per section 3.7: a Consumer rApp cancelling
    its own job, or the DME framework itself tearing down a job it created
    against a Producer rApp (consumer_id == 'DME_FRAMEWORK').
    """
    job = db.get(DataJob, data_job_id)
    if job is not None:
        db.delete(job)
        db.commit()


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


def _type_view(db: Session, t: DMEType) -> dict:
    return {
        "dmeTypeId": str(t.dme_type_id),
        "dmeTypeIdStruct": t.dme_type_id_struct,
        "typeName": t.type_name,
        "producerId": t.producer_id,
        "typeStatus": _computed_type_status(db, t.dme_type_id),  # ADOPT from ICS, section 3.4
        "producerHealthCallbackUrl": t.producer_health_callback_url,
    }


def _computed_type_status(db: Session, dme_type_id: uuid.UUID) -> str:
    active = db.scalar(select(DataJob).where(DataJob.dme_type_id == dme_type_id, DataJob.status == "ACTIVE").limit(1))
    return "ENABLED" if active is not None else "DISABLED"
