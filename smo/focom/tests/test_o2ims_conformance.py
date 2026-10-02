"""SA-FOCOM-2 / 6 / 7 / 9: O-Cloud sites and locations, alarm and performance
subscriptions, artifact / cluster / infrastructure / provisioning resources, and
the closed ResourceType list. Run with: pytest smo/focom/tests -q
"""

import uuid

import pytest

from test_main import client, db_session  # noqa: F401  (pytest fixtures)


@pytest.fixture
def sent(monkeypatch):
    """What the notifications send: they are outbox rows (PR-MSG-1.8), delivered through smo_shared.webhook right after the commit."""
    import httpx
    posts = []
    monkeypatch.setattr("smo_shared.webhook.post_webhook", lambda dest, json, timeout=5.0: posts.append((dest, json)) or httpx.Response(200))
    return posts


# ---------------------------------------------------------------- SA-FOCOM-2

def test_a_site_belongs_to_a_location_and_a_pool_to_a_site(client):
    loc = client.post("/locations", json={"name": "Munich DC", "coordinate": "48.1,11.5"}).json()
    assert loc["objectClass"] == "Location" and loc["oCloudSiteIds"] == []
    site = client.post("/o-cloud-sites", json={"locationId": loc["globalLocationId"], "name": "Munich-1"}).json()
    pool = client.post("/resource-pools", json={"name": "gpu pool", "oCloudSiteId": site["oCloudSiteId"]}).json()
    assert pool["oCloudSiteId"] == site["oCloudSiteId"] and pool["resources"] == []
    assert client.get(f"/locations/{loc['globalLocationId']}").json()["oCloudSiteIds"] == [site["oCloudSiteId"]]
    inline = client.get(f"/o-cloud-sites/{site['oCloudSiteId']}").json()["resourcePools"]
    assert [p["resourcePoolId"] for p in inline] == [pool["resourcePoolId"]]


def test_the_pool_lists_its_resource_ids_and_the_seed_links_pool_0_to_site_0(client):
    pool = client.get("/resource-pools/pool-0").json()
    assert pool["oCloudSiteId"] == "site-0" and pool["resources"] == []
    rid = client.post("/resources/provision", json={"resourceTypeId": "gpu-l40"}).json()["resourceId"]
    assert client.get("/resource-pools/pool-0").json()["resources"] == [rid]


def test_inventory_exposes_the_o2ims_ocloud_fields(client):
    body = client.get("/inventory").json()
    uuid.UUID(body["globalCloudId"])
    assert body["globalCloudId"] == client.get("/inventory").json()["globalCloudId"]  # stable
    assert body["infrastructureManagementServicesEndPoint"] and body["smoRegistrationService"]
    assert body["oCloudSites"][0]["resourcePools"][0]["resourcePoolId"] == "pool-0"
    assert body["locations"][0]["oCloudSiteIds"] == ["site-0"]


def test_dangling_and_duplicate_site_objects_are_refused_and_deletes_are_guarded(client):
    assert client.post("/o-cloud-sites", json={"locationId": "nope", "name": "x"}).status_code == 422
    assert client.post("/resource-pools", json={"name": "x", "oCloudSiteId": "nope"}).status_code == 422
    assert client.post("/locations", json={"globalLocationId": "loc-0", "name": "dup"}).status_code == 422
    assert client.delete("/locations/loc-0").status_code == 422  # still has site-0
    assert client.delete("/o-cloud-sites/site-0").status_code == 422  # still has pool-0
    assert client.get("/locations/nope").status_code == 404 and client.get("/o-cloud-sites/nope").status_code == 404
    loc = client.post("/locations", json={"name": "tmp"}).json()["globalLocationId"]
    assert client.delete(f"/locations/{loc}").status_code == 204


# ---------------------------------------------------------------- SA-FOCOM-6: alarms

def test_an_alarm_is_an_alarm_event_record(client):
    rid = client.post("/resources/provision", json={"resourceTypeId": "gpu-l40"}).json()["resourceId"]
    out = client.post("/alarms/ingest", params={"resource_ref": rid, "severity": "MAJOR", "event_type": "EQUIPMENT_ALARM",
                                                "alarm_definition_id": "gpu-temp", "probable_cause_id": "overheat"}).json()
    alarm = client.get(f"/alarms/{out['alarmId']}").json()
    assert alarm["eventType"] == "EQUIPMENT_ALARM" and alarm["perceivedSeverity"] == "MAJOR" and alarm["severity"] == "major"
    assert alarm["resourceId"] == alarm["resourceRef"] == rid and alarm["resourceTypeId"] == "gpu-l40"
    assert alarm["alarmDefinitionId"] == "gpu-temp" and alarm["alarmAcknowledged"] is False and alarm["alarmRaisedTime"]


def test_alarm_input_is_validated(client):
    assert client.post("/alarms/ingest", params={"resource_ref": "h", "severity": "SEVERE"}).status_code == 422
    assert client.post("/alarms/ingest", params={"resource_ref": "h", "severity": "minor", "event_type": "BOGUS"}).status_code == 422
    assert client.get("/alarms", params={"severity": "SEVERE"}).status_code == 422
    assert client.post("/alarms/ingest", params={"resource_ref": "h", "severity": "INDETERMINATE"}).status_code == 200


def test_ack_clear_and_change_set_times_and_notify_by_filter(client, sent):
    everything = client.post("/alarm-subscriptions", json={"callback": "http://c/all", "consumerSubscriptionId": "mine"}).json()
    client.post("/alarm-subscriptions", json={"callback": "http://c/clear", "filter": "CLEAR"})
    alarm = client.post("/alarms/ingest", params={"resource_ref": "h", "severity": "critical"}).json()["alarmId"]
    assert [d for d, _ in sent] == ["http://c/all"] and sent[0][1]["alarmNotificationType"] == "NEW"
    assert sent[0][1]["consumerSubscriptionId"] == "mine" and sent[0][1]["objectRef"] == f"/focom/alarms/{alarm}"
    uuid.UUID(sent[0][1]["globalCloudId"])
    acked = client.patch(f"/alarms/{alarm}/ack").json()
    assert acked["alarmAcknowledged"] is True and acked["alarmAcknowledgeTime"]
    changed = client.patch(f"/alarms/{alarm}/severity", params={"severity": "minor"}).json()
    assert changed["perceivedSeverity"] == "MINOR" and changed["alarmChangedTime"]
    sent.clear()
    cleared = client.patch(f"/alarms/{alarm}/clear").json()
    assert cleared["perceivedSeverity"] == "CLEARED" and cleared["alarmClearedTime"]
    assert sorted(d for d, _ in sent) == ["http://c/all", "http://c/clear"] and {j["alarmNotificationType"] for _, j in sent} == {"CLEAR"}
    assert client.get("/alarms", params={"severity": "cleared"}).json()["total"] == 1
    assert client.get(f"/alarm-subscriptions/{everything['alarmSubscriptionId']}").json()["callback"] == "http://c/all"
    assert client.delete(f"/alarm-subscriptions/{everything['alarmSubscriptionId']}").status_code == 204
    assert client.get(f"/alarm-subscriptions/{everything['alarmSubscriptionId']}").status_code == 404


def test_alarm_subscription_filter_is_an_enum_and_unknown_alarms_404(client):
    assert client.post("/alarm-subscriptions", json={"callback": "http://c", "filter": "SOMETIMES"}).status_code == 422
    missing = "00000000-0000-0000-0000-000000000000"
    assert client.get(f"/alarms/{missing}").status_code == 404 and client.patch(f"/alarms/{missing}/ack").status_code == 404


def test_the_old_alarm_fields_are_still_there(client):
    client.post("/alarms/ingest", params={"resource_ref": "host-1", "severity": "critical"})
    item = client.get("/alarms").json()["items"][0]
    assert {"alarmId", "resourceRef", "severity"} <= set(item) and item["resourceRef"] == "host-1"


# ---------------------------------------------------------------- SA-FOCOM-6: performance

def _job(client, **kw):
    return client.post("/performance-jobs", json={"consumerPerformanceJobId": "c-1", "collectionInterval": 60, **kw})


def test_a_performance_job_collects_ingested_records(client):
    job = _job(client, qualifiedResourceTypes=["gpu-l40"]).json()
    assert job["state"] == "ACTIVE" and job["status"] == "IDLE" and job["preInstalledJob"] is False and job["collectedMeasurements"] == []
    out = client.post("/performance/ingest", json={"resourceId": "host-1", "performanceMeasurementDefinitionId": "cpu_load",
                                                   "measurementValue": 0.42, "performanceMeasurementJobId": job["performanceMeasurementJobId"]})
    assert out.status_code == 201 and out.json()["value"] == 0.42 and out.json()["measurementValue"] == 0.42
    after = client.get(f"/performance-jobs/{job['performanceMeasurementJobId']}").json()
    assert after["status"] == "RUNNING"
    assert [m["resourceId"] for m in after["measuredResources"]] == ["host-1"] and after["measuredResources"][0]["isCurrentlyMeasured"]
    assert after["collectedMeasurements"][0]["performanceMeasurementDefinitionId"] == "cpu_load"
    assert client.get("/performance", params={"performance_measurement_job_id": job["performanceMeasurementJobId"]}).json()["total"] == 1


def test_an_object_valued_measurement_and_the_old_metric_shape(client):
    client.post("/performance/ingest", json={"resourceId": "h", "performanceMeasurementDefinitionId": "temps", "measurementValue": {"cpu": 61, "gpu": 70}})
    item = client.get("/performance", params={"resource_ref": "h"}).json()["items"][0]
    assert item["measurementValue"] == {"cpu": 61, "gpu": 70} and item["value"] is None
    assert {"resourceRef", "metricName", "value"} <= set(item)


def test_ingest_checks_the_job(client):
    ghost = str(uuid.uuid4())
    body = {"resourceId": "h", "performanceMeasurementDefinitionId": "m", "measurementValue": 1}
    assert client.post("/performance/ingest", json={**body, "performanceMeasurementJobId": ghost}).status_code == 422
    job = _job(client).json()["performanceMeasurementJobId"]
    assert client.patch(f"/performance-jobs/{job}", params={"state": "SUSPENDED"}).json()["state"] == "SUSPENDED"
    assert client.post("/performance/ingest", json={**body, "performanceMeasurementJobId": job}).status_code == 422
    assert client.post("/performance-jobs", json={"collectionInterval": 0}).status_code == 422
    assert client.delete(f"/performance-jobs/{job}").status_code == 204 and client.get(f"/performance-jobs/{job}").status_code == 404


def test_performance_subscriptions_receive_reports_for_matching_job_records(client, sent):
    job = _job(client).json()["performanceMeasurementJobId"]
    sub = client.post("/performance-subscriptions", json={
        "callback": "http://c/perf", "consumerPerformanceSubscriptionId": "p-1",
        "globalSubscriptionCriteria": [{"measurementCriteria": [{"key": "performanceMeasurementDefinitionId", "value": ["cpu_load"]}]}],
        "measurementReportingFrequencies": [{"subscriptionMode": "SAMPLE", "reportInterval": 30}]}).json()
    assert sub["reportFormat"] == "NOTIFICATION"
    base = {"resourceId": "h", "measurementValue": 0.5, "performanceMeasurementJobId": job}
    assert client.post("/performance/ingest", json={**base, "performanceMeasurementDefinitionId": "mem"}).json()["notified"] == 0
    out = client.post("/performance/ingest", json={**base, "performanceMeasurementDefinitionId": "cpu_load"}).json()
    assert out["notified"] == 1
    report = sent[0][1]
    assert report["performanceSubscriptionId"] == sub["performanceSubscriptionId"] and report["consumerPerformanceSubscriptionId"] == "p-1"
    assert report["jobReports"][0]["performanceJobId"] == job and report["jobReports"][0]["consumerJobId"] == "c-1"
    value = report["jobReports"][0]["measuredResources"][0]
    assert value["resourceId"] == "h" and value["measurementValues"][0]["performanceMeasurementId"] == "cpu_load"
    sent.clear()  # a record with no job is stored, never reported
    assert client.post("/performance/ingest", json={"resourceId": "h", "performanceMeasurementDefinitionId": "cpu_load", "measurementValue": 1}).json()["notified"] == 0 and sent == []


def test_performance_subscription_validation(client):
    ok = {"callback": "http://c"}
    assert client.post("/performance-subscriptions", json={**ok, "reportFormat": "FILE"}).status_code == 422
    assert client.post("/performance-subscriptions", json={**ok, "reportFormat": "STREAM"}).status_code == 422
    assert client.post("/performance-subscriptions", json={**ok, "globalSubscriptionCriteria": [{"bogus": []}]}).status_code == 422
    assert client.post("/performance-subscriptions", json={**ok, "globalSubscriptionCriteria": [{"jobCriteria": [{"key": "x", "value": 1}]}]}).status_code == 422
    assert client.post("/performance-subscriptions", json={**ok, "measurementReportingFrequencies": [{"subscriptionMode": "NEVER"}]}).status_code == 422
    created = client.post("/performance-subscriptions", json=ok).json()["performanceSubscriptionId"]
    assert client.get("/performance-subscriptions").json()["total"] == 1
    assert client.delete(f"/performance-subscriptions/{created}").status_code == 204 and client.get(f"/performance-subscriptions/{created}").status_code == 404


# ---------------------------------------------------------------- SA-FOCOM-7

def _make(client, path, body, status=201):
    r = client.post(f"/{path}", json=body)
    assert r.status_code == status, r.text
    return r.json()


def _artifact(client, name="k8s-template", version="1.0"):
    return _make(client, "artifact-resources", {"name": name, "version": version, "description": "d"})["artifactResourceId"]


def test_artifact_and_cluster_resources_use_spec_names(client):
    art_type = _make(client, "artifact-resource-types", {"name": "helm", "description": "d"})
    assert art_type["artifactResourceTypeId"] and art_type["objectClass"] == "ArtifactResourceType"
    art = _artifact(client)
    ctype = _make(client, "node-cluster-types", {"name": "k8s", "description": "d"})["nodeClusterTypeId"]
    cluster = _make(client, "node-clusters", {"clientNodeClusterId": "edge-1", "name": "edge", "description": "d",
                                              "clusterDistributionDescription": "k8s 1.30", "nodeClusterTypeId": ctype, "artifactResourceId": art})
    assert cluster["nodeClusterId"] and cluster["clusterResourceIds"] == [] and cluster["artifactResourceId"] == art
    assert client.get(f"/node-clusters/{cluster['nodeClusterId']}").json()["name"] == "edge"
    assert client.get("/node-clusters").json()["total"] == 1


def test_cluster_resources_and_groups_reference_inventory_and_each_other(client):
    rid = client.post("/resources/provision", json={"resourceTypeId": "pserver"}).json()["resourceId"]
    crt = _make(client, "cluster-resource-types", {"name": "node", "description": "d"})["clusterResourceTypeId"]
    cr = _make(client, "cluster-resources", {"name": "n1", "description": "d", "clusterResourceTypeId": crt, "resourceId": rid})
    group = _make(client, "cluster-resource-groups", {"name": "workers", "description": "d", "clusterResources": [cr["clusterResourceId"]]})
    assert group["clusterResources"] == [cr["clusterResourceId"]]
    _make(client, "cluster-resources", {"name": "n2", "description": "d", "clusterResourceTypeId": crt, "resourceId": "not-a-resource"}, status=422)
    _make(client, "cluster-resources", {"name": "n3", "description": "d", "clusterResourceTypeId": "nope", "resourceId": rid}, status=422)
    _make(client, "cluster-resource-groups", {"name": "g", "description": "d", "clusterResources": ["nope"]}, status=422)
    assert client.delete(f"/cluster-resources/{cr['clusterResourceId']}").status_code == 422  # the group lists it
    assert client.delete(f"/cluster-resource-groups/{group['clusterResourceGroupId']}").status_code == 204
    assert client.delete(f"/cluster-resources/{cr['clusterResourceId']}").status_code == 204


def test_infrastructure_resources(client):
    rid = client.post("/resources/provision", json={"resourceTypeId": "pserver"}).json()["resourceId"]
    art = _artifact(client)
    itype = _make(client, "infrastructure-resource-types", {"name": "gateway", "description": "d"})["infrastructureResourceTypeId"]
    infra = _make(client, "infrastructure-resources", {"name": "gw", "description": "d", "infrastructureResourceTypeId": itype,
                                                       "inventoryResourceIds": [rid], "artifactResourceId": art,
                                                       "extensions": [{"key": "gatewayType", "value": "L3"}]})
    assert infra["infrastructureResourceId"] and infra["extensions"] == [{"key": "gatewayType", "value": "L3"}]
    _make(client, "infrastructure-resources", {"name": "x", "description": "d", "infrastructureResourceTypeId": itype,
                                               "inventoryResourceIds": ["nope"], "artifactResourceId": art}, status=422)
    linked = _make(client, "infrastructure-resources", {"name": "gw2", "description": "d", "infrastructureResourceTypeId": itype, "inventoryResourceIds": [rid],
                                                        "artifactResourceId": art, "linkedInfrastructureResourceIds": [infra["infrastructureResourceId"]]})
    assert client.delete(f"/infrastructure-resources/{infra['infrastructureResourceId']}").status_code == 422
    assert client.delete(f"/artifact-resources/{art}").status_code == 422
    assert client.delete(f"/infrastructure-resources/{linked['infrastructureResourceId']}").status_code == 204


def test_unknown_attributes_and_ids_are_refused(client):
    _make(client, "artifact-resources", {"name": "n", "version": "1", "description": "d", "bogus": 1}, status=422)
    assert client.get("/artifact-resources/nope").status_code == 404
    assert client.delete("/artifact-resources/nope").status_code == 204


def test_a_provisioning_request_is_fulfilled_into_a_node_cluster(client):
    _artifact(client, "ran-site", "2.1")
    ctype = _make(client, "node-cluster-types", {"name": "k8s", "description": "d"})["nodeClusterTypeId"]
    req = _make(client, "provisioning-requests", {"name": "site-a", "description": "d", "templateName": "ran-site", "templateVersion": "2.1",
                                                  "templateParameters": [{"key": "nodeClusterTypeId", "value": ctype}]})
    assert req["objectClass"] == "ProvisioningRequest" and req["status"]["provisioningPhase"] == "FULFILLED"
    cluster_id = req["provisionedResourceSet"]["nodeClusterId"]
    cluster = client.get(f"/node-clusters/{cluster_id}").json()
    assert cluster["name"] == "site-a" and cluster["nodeClusterTypeId"] == ctype and "no cluster is deployed" in cluster["clusterDistributionDescription"]
    assert client.get(f"/provisioning-requests/{req['provisioningRequestId']}").json()["templateName"] == "ran-site"
    assert client.get("/provisioning-requests").json()["total"] == 1
    assert client.delete(f"/provisioning-requests/{req['provisioningRequestId']}").status_code == 204
    assert client.get(f"/node-clusters/{cluster_id}").status_code == 404 and client.get("/provisioning-requests").json()["total"] == 0


def test_a_provisioning_request_needs_a_template_and_a_cluster_type(client):
    body = {"name": "s", "description": "d", "templateName": "ghost", "templateVersion": "1"}
    assert client.post("/provisioning-requests", json=body).status_code == 422  # no such ArtifactResource
    _artifact(client, "ghost", "1")
    assert client.post("/provisioning-requests", json=body).status_code == 422  # no NodeClusterType
    _make(client, "node-cluster-types", {"name": "k8s", "description": "d"})
    assert client.post("/provisioning-requests", json=body).status_code == 201  # the first type is the default
    assert client.delete(f"/provisioning-requests/{uuid.uuid4()}").status_code == 204


# ---------------------------------------------------------------- SA-FOCOM-9

def test_resource_types_can_be_registered_and_then_provisioned(client):
    assert client.post("/resources/provision", json={"resourceTypeId": "fpga-u280"}).status_code == 404
    out = client.post("/resource-types", json={"resourceTypeId": "fpga-u280", "name": "Alveo U280", "resourceKind": "PHYSICAL", "resourceClass": "COMPUTE"})
    assert out.status_code == 201 and out.json()["resourceClass"] == "COMPUTE"
    assert client.post("/resources/provision", json={"resourceTypeId": "fpga-u280"}).status_code == 200
    assert client.post("/resource-types", json={"resourceTypeId": "fpga-u280", "name": "dup"}).status_code == 422
    assert client.post("/resource-types", json={"resourceTypeId": "x", "name": "x", "resourceKind": "WEIRD"}).status_code == 422


def test_the_types_callers_provision_are_seeded(client):
    for type_id in ("generic", "gpu-l40", "pserver"):
        assert client.post("/resources/provision", json={"resourceTypeId": type_id}).status_code == 200
    assert client.post("/resources/provision", json={}).status_code == 200  # defaults to generic
