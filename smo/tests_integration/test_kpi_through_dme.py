"""MGT-11.6/11.7 across services: a standard KPI, computed by RAN NF OAM from stored PM files, is published to DME and read by a consumer the way any
DME data is read: a data job on the KPI's type, then the records of that job."""

import datetime

ME = "gnb-kpi-demo-01"
T0 = datetime.datetime(2026, 10, 1, 12, 0, tzinfo=datetime.UTC)


def ok(resp, *codes):
    assert resp.status_code in (codes or (200, 201, 202, 204)), f"{resp.status_code}: {resp.text}"
    return resp.json() if resp.content else None


def test_a_seeded_kpi_is_published_to_dme_and_an_rapp_reads_it(mesh):
    oam, dme = mesh["ran-nf-oam"], mesh["dme"]
    ok(oam.post("/o1-adaptor-endpoints", json={"managedElementRef": ME, "adaptorUri": "http://mock-o1-adaptor:8000/edit-config",
                                               "protocolSupport": ["NETCONF"], "o1Protocol": "NETCONF", "entityType": "O-DU", "vendorName": "mock-vendor"}))
    ok(oam.post("/pm-subscriptions", params={"managed_element_ref": ME, "counter_type": "LOAD_PERFORMANCE", "delivery_method": "pull"}))
    for minute, prb in ((0, 40.0), (15, 60.0)):
        ok(oam.post("/pm-files", json={"managedElementRef": ME, "counterType": "LOAD_PERFORMANCE", "measurements": [
            {"cellId": "401", "timestamp": (T0 + datetime.timedelta(minutes=minute)).isoformat(), "values": {"RRU.PrbTotDl": prb}}]}))
    seeded = ok(oam.post("/kpi-definitions/standard"))
    assert "dl_prb_utilization" in seeded["created"]

    window = {"from_time": (T0 - datetime.timedelta(hours=1)).isoformat(), "to_time": (T0 + datetime.timedelta(hours=1)).isoformat(), "group_by": "cell"}
    # the first publication registers the KPI's DME type (and has no reader yet)
    first = ok(oam.post("/kpis/dl_prb_utilization/publish", params=window))
    assert first["typeName"] == "RAN.KPI.dl_prb_utilization" and first["dataJobs"] == 0 and first["recordsDelivered"] == 0

    # the rApp finds the type and opens a data job on it, as it would for any DME data
    kpi_type = next(t for t in ok(dme.get("/dme-types", params={"data_category": "RAN"})) if t["typeName"] == "RAN.KPI.dl_prb_utilization")
    job = ok(dme.post("/data-jobs", json={"dataDeliveryMode": "CONTINUOUS", "dmeTypeId": kpi_type["dmeTypeId"], "consumerId": "an-rapp",
                                          "dataDeliveryMethod": "PULL_HTTP"}))
    second = ok(oam.post("/kpis/dl_prb_utilization/publish", params=window))
    assert (second["dataJobs"], second["groups"], second["recordsDelivered"]) == (1, 1, 1)

    records = ok(dme.get(f"/data-jobs/{job['dataJobId']}/records"))
    assert records["total"] == 1
    payload = records["items"][0]["payload"]
    assert payload["kpi"] == "dl_prb_utilization" and payload["value"] == 50.0 and payload["unit"] == "percent"
    assert payload["group"] == {"managedElementRef": ME, "cellId": "401"} and payload["samples"] == 2
