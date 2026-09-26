"""RAN Analytics SMOS.

SMO Design v1.3 section 3.11, extended by RAN Analytics LLD section 1:
RegisterAnalyticsProducer closes the producer-side gap v1.3 left entirely
unmodeled (Subscribe/Unsubscribe/Query existed, nothing for a producer to
register against).

Wave 1 of the AI Platform Service Decomposition narrowed this module:
report publishing, subscriptions, and querying moved to `mdaf/` (see
docs/ownership/MDAF_OWNERSHIP.md) — this module keeps its own
use-case-specific role (traffic/energy/coverage analytics production)
and is now an MDAF consumer rather than the service owning analytics
reporting itself. Producer registration never validated against a
report or subscription before the split either, so this module has no
new cross-service call to make: it just no longer owns those two
tables.
"""

import uuid

from fastapi import Depends, FastAPI
from sqlalchemy import select
from sqlalchemy.orm import Session

from smo_shared.db import get_session
from smo_shared.r1_client import R1Client

from .models import MDAFProducer

app = FastAPI(title="RAN Analytics SMOS")


@app.get("/health")
def health_check():
    """Liveness probe. The GUI BFF's GET /modules/status fans out to
    /<module>/health through R1 Termination for every module in parallel,
    so every module answers one — previously only ran-nf-oam/a1-related
    did (as their own DME producer-health callback URL).
    """
    return {"status": "healthy"}


@app.post("/producers", status_code=201)
def register_analytics_producer(producer_id: str, analytics_type: str, dme_input_types: list[uuid.UUID], output_schema: dict, db: Session = Depends(get_session)):
    """RegisterAnalyticsProducer — an update-in-place upsert on
    (producer_id, analytics_type), not just an insert: the same producer
    re-registering the same analytics_type (e.g. on restart) is a normal
    occurrence, not a conflict, and previously crashed with an unhandled
    IntegrityError on the composite primary key instead. Same shape of
    fix as SME's RegisterService (Foundational Platform LLD section 5).
    """
    prod = db.get(MDAFProducer, (producer_id, analytics_type))
    if prod is None:
        prod = MDAFProducer(producer_id=producer_id, analytics_type=analytics_type)
        db.add(prod)
    prod.dme_input_types = dme_input_types
    prod.output_schema = output_schema
    db.commit()
    # OPEN_ITEMS.md section 5: SME's register_service now requires the
    # apf_id to be a registered publishing function (Provider (APF)
    # enrolment) — this producer must enrol before it can publish itself
    # as an SME service, the same real two-step CAPIF dance the reference
    # itself requires.
    R1Client().post("/sme/provider-registrations", json={"apfId": producer_id})
    R1Client().post("/sme/published-apis/v1/{}/service-apis".format(producer_id), json={
        "serviceName": f"mdaf.{analytics_type}", "producerId": producer_id, "endpoint": "internal",
        "version": "1.0", "serviceCapabilities": {"analyticsType": analytics_type}, "moduleScope": "ran-analytics",
    })
    return {"status": "registered"}


@app.get("/producers")
def list_analytics_producers(analytics_type: str | None = None, producer_id: str | None = None, db: Session = Depends(get_session)):
    """OPEN_ITEMS.md section 5: no list/query endpoint for registered
    producers existed at all — same gap as MDAF's own subscriptions list.
    """
    stmt = select(MDAFProducer)
    if analytics_type:
        stmt = stmt.where(MDAFProducer.analytics_type == analytics_type)
    if producer_id:
        stmt = stmt.where(MDAFProducer.producer_id == producer_id)
    return [_producer_view(p) for p in db.scalars(stmt).all()]


def _producer_view(p: MDAFProducer) -> dict:
    return {"producerId": p.producer_id, "analyticsType": p.analytics_type,
            "dmeInputTypes": [str(t) for t in p.dme_input_types], "outputSchema": p.output_schema}
