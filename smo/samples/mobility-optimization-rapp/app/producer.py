"""Sample handover data for the Mobility Optimization rApp (Wave 10.2,
W10.2-03).

  * `relation_counters` / `pm_report` — hourly per-relation handover counters
    with a daily load shape and an optional injected fault, shaped as the PM
    reports an NF sends RAN NF OAM (`POST /ran-nf-oam/pm-reports`). RAN NF OAM
    delivers them to DME as the HO_PERFORMANCE dataset.
  * The Digital Twin producer registers `HO_PERFORMANCE_SIM` (sourceDomain
    DIGITAL_TWIN) and publishes relations with known injected faults. Each
    record carries its `scenario`, so emulation can score the controller's
    direction.
"""

import datetime
import hashlib

from fastapi import APIRouter
from pydantic import BaseModel

from .model.series import ATTEMPTS, PING_PONG, TOO_EARLY, TOO_LATE, WRONG_CELL

router = APIRouter()

SIM_PRODUCER_ID = "mobility-dt-producer"
SIM_TYPE = {"namespace": "RAN", "name": "HO_PERFORMANCE_SIM", "version": "1.0.0", "typeName": "RAN.HO_PERFORMANCE_SIM"}
SELF_URL = "http://mobility-optimization-rapp:8000"

HEALTHY = {TOO_LATE: 0.004, TOO_EARLY: 0.003, WRONG_CELL: 0.002, PING_PONG: 0.003}
SCENARIOS = {
    "HEALTHY": HEALTHY,
    "TOO_LATE": {**HEALTHY, TOO_LATE: 0.07},
    "TOO_EARLY": {**HEALTHY, TOO_EARLY: 0.05, PING_PONG: 0.015},
    "PING_PONG": {**HEALTHY, PING_PONG: 0.06},
    "WRONG_CELL": {**HEALTHY, WRONG_CELL: 0.06},
}
_LOAD = [(0, 0.05), (5, 0.05), (8, 0.8), (12, 0.9), (18, 1.0), (22, 0.4), (24, 0.05)]


def load(hour: float) -> float:
    for (h0, v0), (h1, v1) in zip(_LOAD, _LOAD[1:]):
        if h0 <= hour <= h1:
            return v0 + (v1 - v0) * (hour - h0) / (h1 - h0)
    return 0.05


def _jitter(key: str, t: datetime.datetime) -> float:
    return (hashlib.sha256(f"{key}{t.isoformat()}".encode()).digest()[0] / 255 - 0.5) * 0.2  # ±10 %


def relation_counters(relation: str, t: datetime.datetime, scenario: str = "HEALTHY", attempts: int | None = None) -> dict:
    att = attempts if attempts is not None else int(60 + 240 * load(t.hour + t.minute / 60))
    rates = SCENARIOS[scenario]
    out = {ATTEMPTS: att}
    for counter, rate in rates.items():
        out[counter] = max(0, round(att * rate * (1 + _jitter(relation + counter, t))))
    return out


def windows(relation: str, start: datetime.datetime, hours: int, scenario: str = "HEALTHY") -> list[tuple[datetime.datetime, dict]]:
    return [(start + datetime.timedelta(hours=h), relation_counters(relation, start + datetime.timedelta(hours=h), scenario))
            for h in range(hours)]


def pm_report(managed_element_ref: str, measurements: list[dict]) -> dict:
    return {"managedElementRef": managed_element_ref, "counterType": "HO_PERFORMANCE", "measurements": measurements}


# ---------------------------------------------------------------- Digital Twin producer (DME callbacks)

class SimPublishRequest(BaseModel):
    managedElementRef: str
    relations: dict[str, str]  # relation id -> injected scenario
    start: datetime.datetime
    hours: int = 24


def register_sim_type(sdk) -> dict:
    return sdk.data.register_type(
        SIM_TYPE["namespace"], SIM_TYPE["name"], SIM_TYPE["version"], SIM_TYPE["typeName"], SIM_PRODUCER_ID,
        data_production_schema={}, producer_health_callback_url=f"{SELF_URL}/sim-producer/health",
        job_callback_url=f"{SELF_URL}/sim-producer/jobs", source_domain="DIGITAL_TWIN",
        source_context={"producer": "mobility-optimization-rapp digital twin", "pattern": "injected MRO faults"})


def publish_sim(sdk, body: SimPublishRequest) -> dict:
    type_id = next(t["dmeTypeId"] for t in sdk.data.discover_types("RAN") if t["dmeTypeIdStruct"]["name"] == SIM_TYPE["name"])
    jobs = sdk.data.list_data_jobs(dme_type_id=type_id)
    count = 0
    for relation, scenario in body.relations.items():
        for t, values in windows(relation, body.start, body.hours, scenario):
            payload = {"managedElementRef": body.managedElementRef, "cellId": relation.split("-")[0], "relation": relation,
                       "values": values, "scenario": scenario, "timestamp": t.isoformat()}
            for job in jobs:
                sdk.data.ingest_data_record(job["dataJobId"], payload)
                count += 1
    return {"dmeTypeId": type_id, "dataJobs": len(jobs), "recordsDelivered": count}


@router.get("/sim-producer/health")
def sim_producer_health():
    return {"status": "healthy"}


@router.post("/sim-producer/jobs")
def sim_producer_job(body: dict):
    return {"status": "accepted"}


@router.delete("/sim-producer/jobs/{data_job_id}", status_code=204)
def sim_producer_job_stop(data_job_id: str):
    pass
