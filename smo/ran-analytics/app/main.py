"""RAN Analytics SMOS.

SMO Design v1.3 section 3.11, extended by RAN Analytics LLD section 1:
RegisterAnalyticsProducer closes the producer-side gap v1.3 left entirely
unmodeled (Subscribe/Unsubscribe/Query existed, nothing for a producer to
register against).

Wave 1 of the AI Platform Service Decomposition narrowed this module:
report publishing, subscriptions, and querying moved to `mdaf/` (see
docs/ARCHITECTURE.md (MDAF)) — this module keeps its own
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

from smo_shared.logconfig import install_logging
from smo_shared.metrics import install_metrics
from smo_shared.health import database_check, install_health, sme_token_check
from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error
from smo_shared.r1_client import R1Client
from smo_shared.openapi_security import apply_r1_gateway_security
from smo_shared.correlation import apply_correlation_id
from smo_shared.pagination import PageLimit, PageOffset, paginate

from .models import MDA_TYPES, MDAFProducer, infer_mda_type

app = FastAPI(title="RAN Analytics SMOS")
install_logging(app)  # structured JSON logs and one access-log line per request (PR-OBS-1)
install_metrics(app)  # /metrics and request count/latency series (PR-OBS-2)
apply_r1_gateway_security(app)
apply_correlation_id(app)


install_health(app, checks=[database_check, sme_token_check])  # /live, /ready and the /health alias (PR-ST-7)


@app.post("/producers", status_code=201)
def register_analytics_producer(producer_id: str, analytics_type: str, dme_input_types: list[uuid.UUID], output_schema: dict,
                                 mda_type: str | None = None, db: Session = Depends(get_session)):
    """RegisterAnalyticsProducer — an update-in-place upsert on
    (producer_id, analytics_type), not just an insert: the same producer
    re-registering the same analytics_type (e.g. on restart) is a normal
    occurrence, not a conflict, and previously crashed with an unhandled
    IntegrityError on the composite primary key instead. Same shape of
    fix as SME's RegisterService (Foundational Platform LLD section 5).

    HISTORY.md §7's `analytics_type` enum finding: `mda_type`, TS28104's
    own real closed MDAType enum, is optional and additive — a caller may
    declare one directly (validated against the real 24 values), or, if
    omitted, `infer_mda_type` derives it for the two shorthand values
    this build already honestly maps; anything else stays `None`, not
    guessed.
    """
    if mda_type is not None and mda_type not in MDA_TYPES:
        raise framework_error(FrameworkError.SCHEMA_VALIDATION_FAILED, detail=f"unknown mda_type {mda_type!r}")
    prod = db.get(MDAFProducer, (producer_id, analytics_type))
    if prod is None:
        prod = MDAFProducer(producer_id=producer_id, analytics_type=analytics_type)
        db.add(prod)
    prod.dme_input_types = dme_input_types
    prod.output_schema = output_schema
    prod.mda_type = mda_type if mda_type is not None else infer_mda_type(analytics_type)
    db.commit()
    # HISTORY.md §5: SME's register_service now requires the
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
def list_analytics_producers(analytics_type: str | None = None, producer_id: str | None = None, limit: int = PageLimit,
                              offset: int = PageOffset, db: Session = Depends(get_session)):
    """HISTORY.md §5: no list/query endpoint for registered
    producers existed at all — same gap as MDAF's own subscriptions list.
    """
    stmt = select(MDAFProducer)
    if analytics_type:
        stmt = stmt.where(MDAFProducer.analytics_type == analytics_type)
    if producer_id:
        stmt = stmt.where(MDAFProducer.producer_id == producer_id)
    page = paginate(db, stmt, limit, offset)
    return {**page, "items": [_producer_view(p) for p in page["items"]]}


def _producer_view(p: MDAFProducer) -> dict:
    return {"producerId": p.producer_id, "analyticsType": p.analytics_type, "mdaType": p.mda_type,
            "dmeInputTypes": [str(t) for t in p.dme_input_types], "outputSchema": p.output_schema}
