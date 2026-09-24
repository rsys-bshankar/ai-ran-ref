"""Proves every request in DEMO_RUNBOOK.md's sequence actually succeeds
against this build's real routes — a regression guard for the runbook
itself, not just the sample package `test_cross_service.py`'s own
onboard/deploy test already covers. If a future change to any of
SME/DME/rApp Mgmt/NFO/FOCOM's request or response shapes breaks this
test, DEMO_RUNBOOK.md's copy-pasted commands would have silently gone
stale too — this is what would have caught it.

Run with: pytest smo/tests_integration -q
"""

from pathlib import Path


def test_full_runbook_sequence_succeeds(mesh, loaded_apps, shared_engine, monkeypatch):
    csar_bytes = (Path(__file__).resolve().parent.parent / "samples" / "hello-world-rapp.csar").read_bytes()

    class FakeResp:
        content = csar_bytes
        def raise_for_status(self):
            pass

    import httpx
    real_get = httpx.get  # the mesh's own installed dispatcher — must still handle every other call

    def fake_get(location, timeout=None, **kwargs):
        if location == "http://example/hello-world-rapp.csar":
            return FakeResp()
        return real_get(location, timeout=timeout, **kwargs)

    monkeypatch.setattr(loaded_apps["onboarding"].httpx, "get", fake_get)

    # DEMO_RUNBOOK.md step 2: onboard
    onboard = mesh["onboarding"].post("/packages", json={"location": "http://example/hello-world-rapp.csar"})
    package_id = onboard.json()["packageId"]
    status = mesh["onboarding"].get(f"/packages/{package_id}/onboarding-status")
    assert status.json()["state"] == "AVAILABLE"

    # step 3: deploy
    create = mesh["rapp-mgmt"].post("/instances", json={"packageId": package_id, "config": {}})
    assert create.status_code == 202
    instance_id = create.json()["instanceId"]

    inv = mesh["focom"].get("/inventory")
    assert inv.status_code == 200

    # step 4: bootstrap — provider/invoker registration, token, service-api publish, DME producer
    boot = mesh["r1-termination"].get("/bootstrap")
    assert boot.status_code == 200
    assert {e["apiName"] for e in boot.json()["apiEndpoints"]} == {"service-apis", "published-apis"}

    prov = mesh["sme"].post("/provider-registrations", json={
        "apfId": "hello-world-rapp", "providerDomainInfo": "Hello World rApp — demo provider domain",
    })
    assert prov.status_code == 201

    # SPEC_AUDIT.md SME item 1: apiInvokerId/onboardingSecret are now
    # server-generated, not client-supplied — the client submits its
    # own public key instead.
    inv_reg = mesh["sme"].post("/invoker-registrations", json={"apiInvokerPublicKey": "demo-rapp-public-key"})
    assert inv_reg.status_code == 201
    invoker = inv_reg.json()

    token = mesh["sme"].post("/oauth2/token", json={
        "grant_type": "client_credentials", "client_id": invoker["apiInvokerId"],
        "client_secret": invoker["onboardingSecret"],
    })
    assert token.status_code == 200
    assert token.json()["token_type"] == "Bearer"

    svc = mesh["sme"].post("/published-apis/v1/hello-world-rapp/service-apis", json={
        "serviceName": "helloworld-api", "producerId": "hello-world-rapp",
        "endpoint": "http://hello-world-rapp:8080/helloworld/v1", "version": "v1",
        "fullApiVersions": ["v1"], "moduleScope": "hello-world-rapp",
    })
    assert svc.status_code == 201

    dme_prod = mesh["dme"].post("/production-capabilities", json={
        "namespace": "demo", "name": "hello-world-metrics", "version": "1.0",
        "typeName": "hello-world-metrics-v1", "producerId": "hello-world-rapp",
        "dataProductionSchema": {"type": "object", "properties": {"greeting": {"type": "string"}}},
        "producerHealthCallbackUrl": "http://hello-world-rapp:8080/health",
        "jobCallbackUrl": "http://hello-world-rapp:8080/dme-jobs",
    })
    assert dme_prod.status_code == 201

    # step 5: bootstrap-complete — DEPLOYING -> RUNNING
    bc = mesh["rapp-mgmt"].post(f"/instances/{instance_id}/bootstrap-complete")
    assert bc.status_code == 200
    assert bc.json()["state"] == "RUNNING"

    get_inst = mesh["rapp-mgmt"].get(f"/instances/{instance_id}")
    assert get_inst.status_code == 200
    assert get_inst.json()["state"] == "RUNNING"

    # step 6: operate
    perf = mesh["rapp-mgmt"].post(f"/instances/{instance_id}/performance", json={"greeting": "hello world", "requestsServed": 1})
    assert perf.status_code == 200

    # step 7: RAN NF OAM closed-loop — register a managed element, dispatch
    # a real CM write, raise/ack/clear a fault alarm
    reg = mesh["ran-nf-oam"].post("/o1-adaptor-endpoints", json={
        "managedElementRef": "demo-o-du-1", "adaptorUri": "http://mock-o1-adaptor:8000/edit-config",
        "protocolSupport": ["NETCONF"], "o1Protocol": "NETCONF", "entityType": "O-DU",
    })
    assert reg.status_code == 201
    assert reg.json()["healthStatus"] == "DISCOVERED"
    endpoint_id = reg.json()["endpointId"]

    hb = mesh["ran-nf-oam"].post(f"/o1-adaptor-endpoints/{endpoint_id}/heartbeat")
    assert hb.status_code == 200
    assert hb.json()["healthStatus"] == "ACTIVE"

    job = mesh["ran-nf-oam"].post("/config-jobs", json={
        "requestedBy": "hello-world-rapp", "scope": "cell",
        "changes": [{"managedElementRef": "demo-o-du-1", "attributeChanges": {"adminState": "UNLOCKED"}}],
    })
    assert job.status_code == 202
    job_status = mesh["ran-nf-oam"].get(f"/config-jobs/{job.json()['jobId']}")
    assert job_status.json()["status"] == "COMPLETED"
    assert job_status.json()["subChanges"][0]["status"] == "APPLIED"

    applied = mesh["mock-o1-adaptor"].get("/edit-config/demo-o-du-1")
    assert applied.json()["attributeChanges"] == {"adminState": "UNLOCKED"}

    # A real partial failure: one healthy, registered ME alongside one
    # that was never registered settles the job as PARTIAL_SUCCESS, not
    # an all-or-nothing outcome — WriteConfigurationChanges' own real
    # per-ME dispatch gate (ENDPOINT_UNREACHABLE), not a scripted one.
    partial_job = mesh["ran-nf-oam"].post("/config-jobs", json={
        "requestedBy": "hello-world-rapp", "scope": "cell",
        "changes": [
            {"managedElementRef": "demo-o-du-1", "attributeChanges": {"adminState": "LOCKED"}},
            {"managedElementRef": "demo-o-du-2-never-registered", "attributeChanges": {"adminState": "LOCKED"}},
        ],
    })
    assert partial_job.status_code == 202
    partial_status = mesh["ran-nf-oam"].get(f"/config-jobs/{partial_job.json()['jobId']}")
    assert partial_status.json()["status"] == "PARTIAL_SUCCESS"
    sub_changes_by_me = {sc["managedElementRef"]: sc for sc in partial_status.json()["subChanges"]}
    assert sub_changes_by_me["demo-o-du-1"]["status"] == "APPLIED"
    assert sub_changes_by_me["demo-o-du-2-never-registered"]["status"] == "REJECTED"
    assert sub_changes_by_me["demo-o-du-2-never-registered"]["rejectionReason"] == "ENDPOINT_UNREACHABLE"

    alarm = mesh["ran-nf-oam"].post("/alarms/ingest", params={
        "source_alarm_id": "demo-alarm-1", "managed_element_ref": "demo-o-du-1",
        "severity": "major", "alarm_type": "EQUIPMENT_ALARM",
    })
    assert alarm.status_code == 200
    alarm_id = alarm.json()["alarmId"]

    ack = mesh["ran-nf-oam"].patch(f"/alarms/{alarm_id}/ack", params={"new_state": "ACKNOWLEDGED", "ack_user_id": "demo-operator"})
    assert ack.status_code == 200
    assert ack.json()["ackState"] == "ACKNOWLEDGED"

    cleared = mesh["ran-nf-oam"].patch(f"/alarms/{alarm_id}/clear", params={"clear_user_id": "demo-operator"})
    assert cleared.status_code == 200
    assert cleared.json()["severity"] == "cleared"
    assert cleared.json()["clearUserId"] == "demo-operator"

    # step 8: FOCOM resource management — subscribe to inventory changes,
    # provision a matching resource, observe the real outbound CREATE
    # notification, deprovision it, observe the real DELETE notification.
    # Intercepted at the same httpx.post call FOCOM's own code makes
    # (_notify_inventory_subscribers), same technique as fake_get above —
    # proves the real, unmodified notification code path fires, not a
    # reimplementation of it.
    notifications = []
    real_post = httpx.post

    def fake_post(location, json=None, timeout=None, **kwargs):
        if location == "http://demo-consumer:9000/inventory-events":
            notifications.append(json)
            raise httpx.ConnectError("no real listener in this test, matching the runbook's own note")
        return real_post(location, json=json, timeout=timeout, **kwargs)

    monkeypatch.setattr(loaded_apps["focom"].httpx, "post", fake_post)

    sub = mesh["focom"].post("/inventory/subscriptions", json={
        "callback": "http://demo-consumer:9000/inventory-events",
        "resourceTypeId": "gpu-l40", "consumerSubscriptionId": "demo-sub-1",
    })
    assert sub.status_code == 201

    provisioned = mesh["focom"].post("/resources/provision", json={"resourceTypeId": "gpu-l40", "description": "demo GPU node"})
    assert provisioned.status_code == 200
    resource_id = provisioned.json()["resourceId"]

    pool_resources = mesh["focom"].get("/resource-pools/pool-0/resources")
    assert any(r["resourceId"] == resource_id for r in pool_resources.json())

    assert len(notifications) == 1
    assert notifications[0]["notificationEventType"] == "CREATE"
    assert notifications[0]["resourceId"] == resource_id
    assert notifications[0]["resourceTypeId"] == "gpu-l40"
    assert notifications[0]["consumerSubscriptionId"] == "demo-sub-1"

    deprovisioned = mesh["focom"].delete(f"/resources/{resource_id}")
    assert deprovisioned.status_code == 200

    assert len(notifications) == 2
    assert notifications[1]["notificationEventType"] == "DELETE"
    assert notifications[1]["resourceId"] == resource_id

    # step 9: Policy Mgmt intent automation — register an RMIH, create a
    # matching Intent, observe the real dispatch notification, retract.
    # Intercepted at the same httpx.post call create_intent makes,
    # same technique as FOCOM's step above.
    intent_notifications = []
    real_post_2 = httpx.post

    def fake_post_2(location, json=None, timeout=None, **kwargs):
        if location == "http://so-smos:8000/intents/notify":
            intent_notifications.append(json)
            raise httpx.ConnectError("no real listener in this test, matching the runbook's own note")
        return real_post_2(location, json=json, timeout=timeout, **kwargs)

    monkeypatch.setattr(loaded_apps["policy-mgmt"].httpx, "post", fake_post_2)

    rmih = mesh["policy-mgmt"].post("/intent-handling-functions", json={
        "rmihId": "so-smos", "smeServiceId": "so-smos-svc",
        "capabilities": [{"supportedExpectationObjectType": "RAN_SUBNETWORK"}],
        "notificationCallbackUri": "http://so-smos:8000/intents/notify",
        "intentHandlingScope": ["RAN"],
    })
    assert rmih.status_code == 201

    intent = mesh["policy-mgmt"].post("/intents", json={
        "expectations": [{"expectationObject": {"objectType": "RAN_SUBNETWORK"}}],
        "rmioId": "hello-world-rapp", "intentHandlingScope": "RAN",
    })
    assert intent.status_code == 201
    intent_id = intent.json()["intentId"]

    assert len(intent_notifications) == 1
    assert intent_notifications[0]["intentId"] == intent_id
    assert intent_notifications[0]["expectationObjectTypes"] == ["RAN_SUBNETWORK"]

    get_intent = mesh["policy-mgmt"].get(f"/intents/{intent_id}")
    assert get_intent.status_code == 200
    assert get_intent.json()["intentAdminState"] == "ACTIVATED"

    del_intent = mesh["policy-mgmt"].delete(f"/intents/{intent_id}")
    assert del_intent.status_code == 204

    del_rmih = mesh["policy-mgmt"].delete("/intent-handling-functions/so-smos")
    assert del_rmih.status_code == 204

    # step 10: retire — terminate then delete
    term = mesh["rapp-mgmt"].post(f"/instances/{instance_id}/terminate")
    assert term.status_code == 200
    assert term.json()["state"] == "UNDEPLOYED"

    delete = mesh["rapp-mgmt"].delete(f"/instances/{instance_id}")
    assert delete.status_code == 204
