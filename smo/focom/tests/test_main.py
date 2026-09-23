"""Tests for FOCOM SMOS (NFO+FOCOM LLD section 1). Run with:
pytest smo/focom/tests -q
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from smo_shared.db import Base, get_session

from app.main import app, PHASE1_CLUSTER_ID, PHASE1_DEPLOYMENT_MANAGER_ID, PHASE1_POOL_ID, PHASE1_RESOURCE_TYPE_ID
from app.models import DeploymentManager, InventorySubscription, OCloudAlarm, OCloudPerformanceMetric, Resource, ResourcePool, ResourceType


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[
        OCloudAlarm.__table__, OCloudPerformanceMetric.__table__, InventorySubscription.__table__,
        ResourceType.__table__, ResourcePool.__table__, Resource.__table__, DeploymentManager.__table__,
    ])
    TestSession = sessionmaker(bind=engine)
    return TestSession


@pytest.fixture
def client(db_session):
    def override_get_session():
        session = db_session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = override_get_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_query_inventory_returns_degenerate_cluster(client):
    resp = client.get("/inventory")
    assert resp.json()["clusterId"] == PHASE1_CLUSTER_ID


def test_query_inventory_echoes_requested_resource_type(client):
    """NFO+FOCOM LLD section 4: NFO's Instantiate passes resource_type
    through to shape the returned resource pool — this is the query
    parameter name NFO must use (resource_type, not resourceType; a
    silent bug this exact mismatch caused before it was fixed).
    """
    resp = client.get("/inventory", params={"resource_type": "gpu-l40"})
    assert resp.json()["resourcePools"][0]["resourceTypeId"] == "gpu-l40"


def test_subscribe_inventory_changes_returns_subscription_id(client):
    resp = client.post("/inventory/subscriptions", json={"callbackUri": "http://consumer/callback"})
    assert resp.status_code == 201
    assert "subscriptionId" in resp.json()


def test_unsubscribe_inventory_changes_removes_subscription(client, db_session):
    sub = client.post("/inventory/subscriptions", json={"callbackUri": "http://consumer/callback"}).json()

    resp = client.delete(f"/inventory/subscriptions/{sub['subscriptionId']}")
    assert resp.status_code == 204

    with db_session() as session:
        assert session.get(InventorySubscription, uuid.UUID(sub["subscriptionId"])) is None


def test_unsubscribe_unknown_inventory_subscription_is_idempotent(client):
    resp = client.delete("/inventory/subscriptions/11111111-1111-1111-1111-111111111111")
    assert resp.status_code == 204


def test_provision_resource_notifies_matching_subscriber(client, monkeypatch):
    """OPEN_ITEMS.md section 5: subscribe_inventory_changes took no
    callback parameter, stored nothing, and delivered nothing. This is
    the headline fix — provisioning a resource now actually reaches a
    matching subscriber's callback.
    """
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    client.post("/inventory/subscriptions", json={"callbackUri": "http://consumer/callback", "resourceTypeId": "gpu-l40"})

    resp = client.post("/resources/provision", json={"resourceTypeId": "gpu-l40"})
    resource_id = resp.json()["resourceId"]

    assert len(calls) == 1
    assert calls[0][0] == "http://consumer/callback"
    assert calls[0][1]["notificationEventType"] == "CREATE"
    assert calls[0][1]["resourceId"] == resource_id
    assert calls[0][1]["resourceTypeId"] == "gpu-l40"


def test_provision_resource_does_not_notify_subscriber_filtered_out_by_type(client, monkeypatch):
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    client.post("/inventory/subscriptions", json={"callbackUri": "http://consumer/callback", "resourceTypeId": "gpu-l40"})

    client.post("/resources/provision", json={"resourceTypeId": "generic"})

    assert calls == []


def test_deprovision_resource_notifies_subscriber_regardless_of_type_filter(client, monkeypatch):
    """resource_id here was never actually provisioned, so its type is
    unknowable — a type-filtered subscriber must still be notified
    rather than silently missing the delete event, same as any other
    unset-filter match.
    """
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    client.post("/inventory/subscriptions", json={"callbackUri": "http://consumer/callback", "resourceTypeId": "gpu-l40"})

    client.delete("/resources/some-resource-id")

    assert len(calls) == 1
    assert calls[0][1]["notificationEventType"] == "DELETE"
    assert calls[0][1]["resourceId"] == "some-resource-id"


def test_deprovision_known_resource_notifies_with_its_real_type(client, monkeypatch):
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))

    client.post("/inventory/subscriptions", json={"callbackUri": "http://consumer/callback", "resourceTypeId": "gpu-l40"})
    provisioned = client.post("/resources/provision", json={"resourceTypeId": "gpu-l40"}).json()
    calls.clear()  # discard the provision-time CREATE notification

    client.delete(f"/resources/{provisioned['resourceId']}")

    assert len(calls) == 1
    assert calls[0][1]["notificationEventType"] == "DELETE"
    assert calls[0][1]["resourceTypeId"] == "gpu-l40"


def test_inventory_notification_delivery_survives_unreachable_subscriber(client, monkeypatch):
    import httpx as httpx_module

    def raise_error(url, json=None, timeout=None):
        raise httpx_module.ConnectError("unreachable")

    monkeypatch.setattr("app.main.httpx.post", raise_error)

    client.post("/inventory/subscriptions", json={"callbackUri": "http://consumer/callback"})

    resp = client.post("/resources/provision", json={"resourceTypeId": "generic"})  # must not raise
    assert resp.status_code == 200


def test_provision_and_deprovision_resource(client):
    provisioned = client.post("/resources/provision", json={"cpu": 4, "memory": "16Gi"})
    assert provisioned.status_code == 200
    body = provisioned.json()
    assert body["clusterId"] == PHASE1_CLUSTER_ID
    assert "resourceId" in body

    deprovisioned = client.delete(f"/resources/{body['resourceId']}")
    assert deprovisioned.json()["status"] == "deprovisioned"


def test_monitor_resource_reports_healthy(client):
    resp = client.get("/resources/some-resource-id/status")
    assert resp.json() == {"resourceId": "some-resource-id", "status": "healthy"}


def test_ingested_alarm_is_queryable(client):
    """FOCOM's own alarm domain — infrastructure, distinct from RAN NF
    OAM's RAN-function Alarm (NFO+FOCOM LLD section 1).
    """
    client.post("/alarms/ingest", params={"resource_ref": "host-1", "severity": "critical"})
    resp = client.get("/alarms")
    assert len(resp.json()) == 1
    assert resp.json()[0]["resourceRef"] == "host-1"


def test_performance_metrics_filterable_by_resource(client, db_session):
    """No POST /performance route exists — metrics arrive via O2ims
    collection, not an rApp-facing write — so this seeds directly against
    the same DB the app is wired to, rather than skipping the test.
    """
    session = db_session()
    session.add(OCloudPerformanceMetric(resource_ref="host-1", metric_name="cpu", value=0.5))
    session.add(OCloudPerformanceMetric(resource_ref="host-2", metric_name="cpu", value=0.9))
    session.commit()
    session.close()

    resp = client.get("/performance", params={"resource_ref": "host-2"})
    assert len(resp.json()) == 1
    assert resp.json()[0]["value"] == 0.9


def test_performance_metrics_without_filter_returns_all(client, db_session):
    """The unfiltered path (no resource_ref) was never actually exercised
    — every previous test passed a filter.
    """
    session = db_session()
    session.add(OCloudPerformanceMetric(resource_ref="host-1", metric_name="cpu", value=0.5))
    session.add(OCloudPerformanceMetric(resource_ref="host-2", metric_name="cpu", value=0.9))
    session.commit()
    session.close()

    resp = client.get("/performance")
    assert len(resp.json()) == 2


def test_query_performance_returns_empty_list_when_none_seeded(client):
    resp = client.get("/performance")
    assert resp.json() == []


def test_query_inventory_defaults_resource_type_to_generic(client):
    """The no-resource_type fallback ("generic") was only ever exercised
    implicitly through test_query_inventory_returns_degenerate_cluster,
    which never actually checked resourcePools' resourceTypeId.
    """
    resp = client.get("/inventory")
    assert resp.json()["resourcePools"][0]["resourceTypeId"] == "generic"


def test_query_alarms_returns_empty_list_when_none_ingested(client):
    resp = client.get("/alarms")
    assert resp.json() == []


def test_multiple_alarms_are_all_returned(client):
    """test_ingested_alarm_is_queryable only ever ingested one alarm — a
    second one arriving must not overwrite or drop the first.
    """
    client.post("/alarms/ingest", params={"resource_ref": "host-1", "severity": "critical"})
    client.post("/alarms/ingest", params={"resource_ref": "host-2", "severity": "minor"})

    resp = client.get("/alarms")
    assert len(resp.json()) == 2
    refs = {a["resourceRef"] for a in resp.json()}
    assert refs == {"host-1", "host-2"}


def test_deprovision_arbitrary_unprovisioned_resource_succeeds(client):
    """Phase 1: deprovision_resource never rejects a resource_id that
    was never actually provisioned — a real, explicit behavior worth
    asserting directly rather than only exercising it incidentally
    through the provision-then-deprovision happy path.
    """
    resp = client.delete("/resources/never-provisioned-id")
    assert resp.status_code == 200
    assert resp.json()["status"] == "deprovisioned"


def test_list_resource_types_returns_seeded_phase1_type(client):
    """OPEN_ITEMS.md section 5: no ResourceType/ResourcePool/
    DeploymentManager schema existed at all, and no drill-down
    endpoints existed either. Phase 1's degenerate topology is now
    real, seeded rows, not a hardcoded literal.
    """
    resp = client.get("/resource-types")
    ids = [t["resourceTypeId"] for t in resp.json()]
    assert ids == [PHASE1_RESOURCE_TYPE_ID]


def test_get_resource_type_by_id(client):
    resp = client.get(f"/resource-types/{PHASE1_RESOURCE_TYPE_ID}")
    assert resp.status_code == 200
    assert resp.json()["resourceTypeId"] == PHASE1_RESOURCE_TYPE_ID


def test_get_unknown_resource_type_is_404(client):
    resp = client.get("/resource-types/does-not-exist")
    assert resp.status_code == 404


def test_provision_with_unrecognized_type_auto_registers_it(client):
    """provision_resource never validated resourceTypeId before this
    pass — auto-registering an unrecognized one preserves that, rather
    than rejecting it now that a real ResourceType table exists.
    """
    client.post("/resources/provision", json={"resourceTypeId": "gpu-l40"})

    resp = client.get("/resource-types")
    ids = {t["resourceTypeId"] for t in resp.json()}
    assert ids == {PHASE1_RESOURCE_TYPE_ID, "gpu-l40"}


def test_list_resource_pools_returns_seeded_phase1_pool(client):
    resp = client.get("/resource-pools")
    ids = [p["resourcePoolId"] for p in resp.json()]
    assert ids == [PHASE1_POOL_ID]


def test_get_resource_pool_by_id(client):
    resp = client.get(f"/resource-pools/{PHASE1_POOL_ID}")
    assert resp.status_code == 200
    assert resp.json()["oCloudId"] == PHASE1_CLUSTER_ID


def test_get_unknown_resource_pool_is_404(client):
    resp = client.get("/resource-pools/does-not-exist")
    assert resp.status_code == 404


def test_list_pool_resources_is_empty_before_any_provisioning(client):
    resp = client.get(f"/resource-pools/{PHASE1_POOL_ID}/resources")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_pool_resources_reflects_provisioned_resource(client):
    """provision_resource previously returned a random UUID and
    persisted nothing — this is the headline fix: a provisioned
    resource now actually shows up in its pool's resource list.
    """
    provisioned = client.post("/resources/provision", json={"resourceTypeId": "gpu-l40"}).json()

    resp = client.get(f"/resource-pools/{PHASE1_POOL_ID}/resources")
    resources = resp.json()
    assert len(resources) == 1
    assert resources[0]["resourceId"] == provisioned["resourceId"]
    assert resources[0]["resourceTypeId"] == "gpu-l40"


def test_deprovisioned_resource_no_longer_listed(client):
    provisioned = client.post("/resources/provision", json={"resourceTypeId": "gpu-l40"}).json()
    client.delete(f"/resources/{provisioned['resourceId']}")

    resp = client.get(f"/resource-pools/{PHASE1_POOL_ID}/resources")
    assert resp.json() == []


def test_list_resources_for_unknown_pool_is_404(client):
    resp = client.get("/resource-pools/does-not-exist/resources")
    assert resp.status_code == 404


def test_list_deployment_managers_returns_seeded_phase1_manager(client):
    resp = client.get("/deployment-managers")
    ids = [d["deploymentManagerId"] for d in resp.json()]
    assert ids == [PHASE1_DEPLOYMENT_MANAGER_ID]


def test_get_deployment_manager_by_id(client):
    resp = client.get(f"/deployment-managers/{PHASE1_DEPLOYMENT_MANAGER_ID}")
    assert resp.status_code == 200
    assert resp.json()["oCloudId"] == PHASE1_CLUSTER_ID


def test_get_unknown_deployment_manager_is_404(client):
    resp = client.get("/deployment-managers/does-not-exist")
    assert resp.status_code == 404


def test_topology_export_includes_phase1_seeded_entities(client):
    """OPEN_ITEMS.md section 5: FOCOM had no typed entity/relationship
    model and no /topology-shaped endpoint at all — not even a stub —
    despite the Blueprint naming FOCOM's placement as a TEIV data
    source. Phase 1's seeded ResourceType/ResourcePool/DeploymentManager
    must show up even before any resource is ever provisioned.
    """
    resp = client.get("/topology")
    assert resp.status_code == 200
    body = resp.json()

    entity_types = {key for group in body["entities"] for key in group}
    assert entity_types == {
        "o-ran-smo-teiv-cloud:ResourceType",
        "o-ran-smo-teiv-cloud:ResourcePool",
        "o-ran-smo-teiv-cloud:DeploymentManager",
    }
    # No resources provisioned yet, so no Resource entities and no relationships at all.
    assert body["relationships"] == []

    resource_types = next(g["o-ran-smo-teiv-cloud:ResourceType"] for g in body["entities"] if "o-ran-smo-teiv-cloud:ResourceType" in g)
    assert resource_types[0]["id"] == f"urn:oran:smo:teiv:ResourceType:{PHASE1_RESOURCE_TYPE_ID}"
    assert resource_types[0]["attributes"]["name"] == "generic"


def test_topology_export_includes_provisioned_resource_and_its_relationships(client):
    """The actual fix: a provisioned Resource now shows up as a real
    typed entity, with real FK-backed relationships to its
    ResourceType and ResourcePool — matching the wire shape the
    reference's focom-to-teiv-adapter itself produces (id/attributes
    for entities, id/aSide/bSide/sourceIds for relationships).
    """
    provisioned = client.post("/resources/provision", json={"resourceTypeId": "gpu-l40"}).json()
    resource_id = provisioned["resourceId"]

    body = client.get("/topology").json()

    resource_entities = next(g["o-ran-smo-teiv-cloud:Resource"] for g in body["entities"] if "o-ran-smo-teiv-cloud:Resource" in g)
    assert len(resource_entities) == 1
    assert resource_entities[0]["id"] == f"urn:oran:smo:teiv:Resource:{resource_id}"
    assert resource_entities[0]["attributes"]["resourceTypeId"] == "gpu-l40"
    assert resource_entities[0]["attributes"]["resourcePoolId"] == PHASE1_POOL_ID

    rel_types = {key for group in body["relationships"] for key in group}
    assert rel_types == {
        "o-ran-smo-teiv-cloud:RESOURCE_IS_OF_TYPE_RESOURCETYPE",
        "o-ran-smo-teiv-cloud:RESOURCE_CONTAINED_IN_RESOURCEPOOL",
    }

    is_of_type = next(g["o-ran-smo-teiv-cloud:RESOURCE_IS_OF_TYPE_RESOURCETYPE"] for g in body["relationships"] if "o-ran-smo-teiv-cloud:RESOURCE_IS_OF_TYPE_RESOURCETYPE" in g)
    assert is_of_type[0]["aSide"] == f"urn:oran:smo:teiv:Resource:{resource_id}"
    assert is_of_type[0]["bSide"] == "urn:oran:smo:teiv:ResourceType:gpu-l40"
    assert is_of_type[0]["sourceIds"] == [resource_id, "gpu-l40"]


def test_topology_export_reflects_child_resource_relationship(client, db_session):
    """Resource's own parentId column (already modeling the reference's
    parent/child resource tree) must show up as a real
    RESOURCE_CHILD_OF_RESOURCE relationship in the export, not just sit
    unused in the drill-down view.
    """
    parent_id = client.post("/resources/provision", json={"resourceTypeId": "gpu-l40"}).json()["resourceId"]
    child_id = client.post("/resources/provision", json={"resourceTypeId": "gpu-l40"}).json()["resourceId"]

    with db_session() as session:
        child = session.get(Resource, uuid.UUID(child_id))
        child.parent_id = uuid.UUID(parent_id)
        session.commit()

    body = client.get("/topology").json()
    child_of = next(g["o-ran-smo-teiv-cloud:RESOURCE_CHILD_OF_RESOURCE"] for g in body["relationships"] if "o-ran-smo-teiv-cloud:RESOURCE_CHILD_OF_RESOURCE" in g)
    assert len(child_of) == 1
    assert child_of[0]["aSide"] == f"urn:oran:smo:teiv:Resource:{child_id}"
    assert child_of[0]["bSide"] == f"urn:oran:smo:teiv:Resource:{parent_id}"
