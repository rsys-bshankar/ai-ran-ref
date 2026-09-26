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

    # A real validation failure: onboarding the exact same CSAR a second
    # time hits _validate_package's own real duplicate-content check
    # (matching integrity_hash) — a genuine FAILED outcome via the async
    # onboarding-status contract (still 202 synchronously), not a bug,
    # and it must not disturb the first, already-AVAILABLE package.
    duplicate_onboard = mesh["onboarding"].post("/packages", json={"location": "http://example/hello-world-rapp.csar"})
    assert duplicate_onboard.status_code == 202
    duplicate_package_id = duplicate_onboard.json()["packageId"]
    duplicate_status = mesh["onboarding"].get(f"/packages/{duplicate_package_id}/onboarding-status")
    assert duplicate_status.json()["state"] == "FAILED"

    still_available = mesh["onboarding"].get(f"/packages/{package_id}/onboarding-status")
    assert still_available.json()["state"] == "AVAILABLE"

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

    # FOCOM FCAPS — a distinct domain from RAN NF OAM's RAN-function
    # alarms: real infrastructure/O-Cloud alarm ingest + query, plus
    # performance query (no ingest route exists in this build — real
    # O2ims collection elision — so it's asserted empty, honestly, not
    # skipped).
    alarm = mesh["focom"].post("/alarms/ingest", params={"resource_ref": "phase1-degenerate-cluster", "severity": "critical"})
    assert alarm.status_code == 200
    alarms = mesh["focom"].get("/alarms")
    assert any(a["resourceRef"] == "phase1-degenerate-cluster" and a["severity"] == "critical" for a in alarms.json())

    performance = mesh["focom"].get("/performance")
    assert performance.status_code == 200
    assert performance.json() == []

    # step 10: Policy Mgmt intent automation — register an RMIH, create a
    # matching Intent, observe the real dispatch notification, retract.
    # Intercepted at the same httpx.post call create_intent makes,
    # same technique as FOCOM's step above.
    intent_notifications = []
    sa_smos_notifications = []
    real_post_2 = httpx.post

    def fake_post_2(location, json=None, timeout=None, **kwargs):
        if location == "http://so-smos:8000/intents/notify":
            intent_notifications.append(json)
            raise httpx.ConnectError("no real listener in this test, matching the runbook's own note")
        if location == "http://sa-smos:8000/intents/notify":
            sa_smos_notifications.append(json)
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

    # A real negative case: a second RMIH with the same capability but a
    # different declared scope (CN-only) must NOT be notified of a
    # RAN-scoped Intent, even though its capability matches — proves
    # intentHandlingScope is a genuine pre-filter (_matching_rmihs), not
    # decoration.
    rmih2 = mesh["policy-mgmt"].post("/intent-handling-functions", json={
        "rmihId": "sa-smos", "smeServiceId": "sa-smos-svc",
        "capabilities": [{"supportedExpectationObjectType": "RAN_SUBNETWORK"}],
        "notificationCallbackUri": "http://sa-smos:8000/intents/notify",
        "intentHandlingScope": ["CN"],
    })
    assert rmih2.status_code == 201

    intent2 = mesh["policy-mgmt"].post("/intents", json={
        "expectations": [{"expectationObject": {"objectType": "RAN_SUBNETWORK"}}],
        "rmioId": "hello-world-rapp", "intentHandlingScope": "RAN",
    })
    assert intent2.status_code == 201
    intent2_id = intent2.json()["intentId"]

    assert len(intent_notifications) == 2  # so-smos notified again, for this second intent
    assert intent_notifications[1]["intentId"] == intent2_id
    assert sa_smos_notifications == []  # sa-smos never notified — CN-only scope filtered it out

    del_intent = mesh["policy-mgmt"].delete(f"/intents/{intent_id}")
    assert del_intent.status_code == 204
    del_intent2 = mesh["policy-mgmt"].delete(f"/intents/{intent2_id}")
    assert del_intent2.status_code == 204

    del_rmih = mesh["policy-mgmt"].delete("/intent-handling-functions/so-smos")
    assert del_rmih.status_code == 204
    del_rmih2 = mesh["policy-mgmt"].delete("/intent-handling-functions/sa-smos")
    assert del_rmih2.status_code == 204

    # step 11: A1 Policy Management — register a service, create a real
    # policy against the mock Near-RT RIC, observe a real duplicate-
    # content rejection, observe a real status-change notification,
    # retract. Intercepted at the same httpx.post call
    # _notify_policy_status_subscribers makes, same technique as steps
    # 8-9 above.
    policy_notifications = []
    real_post_3 = httpx.post

    def fake_post_3(location, json=None, timeout=None, **kwargs):
        if location == "http://demo-consumer:9000/policy-status":
            policy_notifications.append(json)
            raise httpx.ConnectError("no real listener in this test, matching the runbook's own note")
        return real_post_3(location, json=json, timeout=timeout, **kwargs)

    monkeypatch.setattr(loaded_apps["a1-related"].httpx, "post", fake_post_3)

    service = mesh["a1-related"].put("/services", json={"serviceId": "hello-world-rapp", "keepAliveIntervalSeconds": 0})
    assert service.status_code == 200

    policy_types = mesh["a1-related"].get("/policy-types")
    assert policy_types.status_code == 200
    assert any(t["policyTypeId"] == "ORAN_QoSandTSP_6.0.1" for t in policy_types.json())

    policy_object = {"scope": {"cellId": "demo-cell-1"}, "qosObjectives": {"gfbr": 100}}
    policy = mesh["a1-related"].post("/policies", json={
        "policyTypeId": "ORAN_QoSandTSP_6.0.1", "policyObject": policy_object,
        "nearRtRicId": "mock-near-rt-ric-001", "creatorId": "hello-world-rapp",
    })
    assert policy.status_code == 201
    assert policy.json()["enforcementStatus"] == "ENFORCED"
    policy_id = policy.json()["policyId"]

    sub = mesh["a1-related"].post("/policies/subscriptions", json={
        "notificationDestination": "http://demo-consumer:9000/policy-status", "policyIdList": [policy_id],
    })
    assert sub.status_code == 201

    duplicate = mesh["a1-related"].post("/policies", json={
        "policyTypeId": "ORAN_QoSandTSP_6.0.1", "policyObject": policy_object,
        "nearRtRicId": "mock-near-rt-ric-001", "creatorId": "hello-world-rapp",
    })
    assert duplicate.status_code == 201
    assert duplicate.json()["enforcementStatus"] == "REJECTED"
    duplicate_policy_id = duplicate.json()["policyId"]

    updated = mesh["a1-related"].put(f"/policies/{policy_id}", json={})
    assert updated.status_code == 200
    assert updated.json()["enforcementStatus"] == "REJECTED"

    assert len(policy_notifications) == 1
    assert policy_notifications[0]["policyId"] == policy_id
    assert policy_notifications[0]["enforcementStatus"] == "REJECTED"

    del_policy = mesh["a1-related"].delete(f"/policies/{policy_id}")
    assert del_policy.status_code == 204
    del_duplicate = mesh["a1-related"].delete(f"/policies/{duplicate_policy_id}")
    assert del_duplicate.status_code == 204
    del_service = mesh["a1-related"].delete("/services/hello-world-rapp")
    assert del_service.status_code == 204

    # step 12: SME Trusted Invokers — register a real security context
    # for the invoker registered in step 4, confirm default redaction,
    # confirm real values on request, revoke, confirm removal.
    register_ti = mesh["sme"].put(f"/trusted-invokers/{invoker['apiInvokerId']}", json={
        "notificationDestination": "http://demo-consumer:9000/security-notify",
        "securityInfo": [{"aefId": "hello-world-rapp", "apiId": "helloworld-api", "authenticationInfo": "demo-auth-info",
                           "authorizationInfo": "demo-authz-info", "prefSecurityMethods": ["OAUTH"]}],
    })
    assert register_ti.status_code == 201
    assert register_ti.json()["securityInfo"][0]["selSecurityMethod"] == "OAUTH"

    redacted = mesh["sme"].get(f"/trusted-invokers/{invoker['apiInvokerId']}")
    assert redacted.status_code == 200
    assert redacted.json()["securityInfo"][0]["authenticationInfo"] == ""
    assert redacted.json()["securityInfo"][0]["authorizationInfo"] == ""

    revealed = mesh["sme"].get(f"/trusted-invokers/{invoker['apiInvokerId']}", params={"authentication_info": True, "authorization_info": True})
    assert revealed.json()["securityInfo"][0]["authenticationInfo"] == "demo-auth-info"
    assert revealed.json()["securityInfo"][0]["authorizationInfo"] == "demo-authz-info"

    revoke = mesh["sme"].post(f"/trusted-invokers/{invoker['apiInvokerId']}/delete", json={
        "aefId": "hello-world-rapp", "apiIds": ["helloworld-api"], "apiInvokerId": invoker["apiInvokerId"], "cause": "UNEXPECTED_REASON",
    })
    assert revoke.status_code == 204

    gone = mesh["sme"].get(f"/trusted-invokers/{invoker['apiInvokerId']}")
    assert gone.status_code == 404

    # step 13: AI/ML Workflow — register a model, request training, upload
    # a real artifact, write metrics, advance the real lifecycle FSM to
    # ACTIVE, download the artifact back, deregister.
    model = mesh["ai-ml-workflow"].post("/models", json={
        "modelType": "hello-world-anomaly-detector", "version": "1.0.0",
        "description": "Demo anomaly-detection model for the hello-world rApp",
        "author": "hello-world-rapp", "owner": "hello-world-rapp",
        "inputDataType": "application/json", "outputDataType": "application/json",
    })
    assert model.status_code == 201
    model_id = model.json()["modelId"]
    assert model.json()["state"] == "REGISTERED"

    training = mesh["ai-ml-workflow"].post("/training-jobs", json={
        "modelId": model_id, "producerId": "hello-world-rapp", "runId": "demo-run-1",
        "trainingDataset": "s3://demo/hello-world-train", "validationDataset": "s3://demo/hello-world-val",
    })
    assert training.status_code == 201
    training_job_id = training.json()["trainingJobId"]

    training_state = mesh["ai-ml-workflow"].get(f"/models/{model_id}")
    assert training_state.json()["state"] == "TRAINING"

    artifact_bytes = b"demo-model-weights-bytes"
    artifact = mesh["ai-ml-workflow"].post(f"/models/{model_id}/artifact",
                                            files={"file": ("hello-world-model.zip", artifact_bytes, "application/zip")})
    assert artifact.status_code == 201
    assert artifact.json()["artifactVersion"] == 1

    metrics = mesh["ai-ml-workflow"].post(f"/training-jobs/{training_job_id}/model-metrics", json={"accuracy": 0.94, "f1Score": 0.91})
    assert metrics.status_code == 200
    assert metrics.json()["modelMetrics"] == {"accuracy": 0.94, "f1Score": 0.91}

    for event in ["TRAINING_COMPLETE", "VALIDATION_COMPLETE", "CERTIFY", "LOAD", "ACTIVATE"]:
        advanced = mesh["ai-ml-workflow"].post(f"/models/{model_id}/advance", params={"event": event})
        assert advanced.status_code == 200
    assert advanced.json()["state"] == "ACTIVE"

    downloaded = mesh["ai-ml-workflow"].get(f"/models/{model_id}/artifact/1")
    assert downloaded.status_code == 200
    assert downloaded.content == artifact_bytes

    deregistered = mesh["ai-ml-workflow"].delete(f"/models/{model_id}")
    assert deregistered.status_code == 204

    # step 14: RAN Analytics — register a producer (real cross-module SME
    # enrolment + service publish), subscribe with a real notification
    # destination, publish a report, observe the real notification fire
    # (same intercept technique as FOCOM/Policy Mgmt/A1 Related above),
    # unsubscribe.
    analytics_notifications = []
    real_post_4 = httpx.post

    def fake_post_4(location, json=None, timeout=None, **kwargs):
        if location == "http://demo-consumer:9000/analytics-reports":
            analytics_notifications.append(json)
            raise httpx.ConnectError("no real listener in this test, matching the runbook's own note")
        return real_post_4(location, json=json, timeout=timeout, **kwargs)

    monkeypatch.setattr(loaded_apps["ran-analytics"].httpx, "post", fake_post_4)

    producer = mesh["ran-analytics"].post("/producers",
        params={"producer_id": "hello-world-rapp", "analytics_type": "coverage-issue-analysis"},
        json={"dme_input_types": [], "output_schema": {"type": "object", "properties": {"issue": {"type": "string"}}}})
    assert producer.status_code == 201

    producers = mesh["ran-analytics"].get("/producers", params={"analytics_type": "coverage-issue-analysis"})
    assert any(p["producerId"] == "hello-world-rapp" for p in producers.json())

    subscription = mesh["ran-analytics"].post("/subscriptions", params={
        "analytics_type": "coverage-issue-analysis", "requested_by": "sa-smos",
        "notification_destination": "http://demo-consumer:9000/analytics-reports",
    })
    assert subscription.status_code == 201
    subscription_id = subscription.json()["subscriptionId"]

    report = mesh["ran-analytics"].post("/reports", params={"analytics_type": "coverage-issue-analysis"},
        json={"output": {"issue": "demo-cell-1 coverage hole detected"}, "input_sources": []})
    assert report.status_code == 201
    report_id = report.json()["reportId"]

    assert len(analytics_notifications) == 1
    assert analytics_notifications[0]["reportId"] == report_id
    assert analytics_notifications[0]["output"] == {"issue": "demo-cell-1 coverage hole detected"}

    reports = mesh["ran-analytics"].get("/reports", params={"analytics_type": "coverage-issue-analysis"})
    assert any(r["reportId"] == report_id for r in reports.json())

    unsubscribed = mesh["ran-analytics"].delete(f"/subscriptions/{subscription_id}")
    assert unsubscribed.status_code == 204

    # step 15: SA SMOS — a real assurance monitor, a genuine RECONNECT
    # heal (resolving a concrete nfDeploymentId via a live SO SMOS order
    # lookup), and a genuine ROLLBACK refusal. RECONNECT needs a real,
    # RUNNING NFDeployment distinct from the sample rApp's own deployment
    # above (NFO's real duplication guard means a descriptor can only be
    # deployed once), so this creates a second descriptor against the
    # same already-onboarded package, then deploys it via a real SO SMOS
    # order (SO SMOS's own dispatch table, exercised further by step 16
    # below).
    descriptor2 = mesh["nfo"].post("/descriptors", json={"packageId": package_id, "name": "sa-smos-demo-descriptor"})
    assert descriptor2.status_code == 201
    descriptor2_id = descriptor2.json()["nfDeploymentDescriptorId"]

    deploy_order = mesh["so-smos"].post("/orders", json={
        "scope": "sa-smos-demo-deploy",
        "steps": [
            {"stepType": "DEPLOY", "targetModule": "NFO", "nfDeploymentDescriptorId": descriptor2_id, "name": "sa-smos-demo-deployment"},
        ],
    })
    assert deploy_order.status_code == 202
    deploy_steps = deploy_order.json()["steps"]
    assert deploy_steps[0]["status"] == "COMPLETED"
    assert deploy_steps[0]["result"]["state"] == "RUNNING"
    sa_smos_order_id = deploy_order.json()["orderId"]
    nf_deployment_id = deploy_steps[0]["result"]["nfDeploymentId"]

    monitor = mesh["sa-smos"].post("/monitors", params={"target_order_id": sa_smos_order_id}, json={"latency": 100})
    assert monitor.status_code == 201
    monitor_id = monitor.json()["monitorId"]

    evaluated = mesh["sa-smos"].post(f"/monitors/{monitor_id}/evaluate", json={"latency": 80})
    assert evaluated.json()["breaches"] == {"latency": 100}

    reconnect = mesh["sa-smos"].post(f"/monitors/{monitor_id}/remedial-actions", params={"action_type": "RECONNECT"})
    assert reconnect.status_code == 201
    assert reconnect.json()["outcome"] == "RESOLVED"

    rollback = mesh["sa-smos"].post(f"/monitors/{monitor_id}/remedial-actions", params={"action_type": "ROLLBACK"})
    assert rollback.status_code == 501
    assert rollback.json()["detail"]["title"] == "ROLLBACK_HISTORY_UNAVAILABLE"

    terminate_second = mesh["nfo"].delete(f"/deployments/{nf_deployment_id}")
    assert terminate_second.status_code == 204

    # step 16: SO SMOS — a real multi-step order dispatched over the real
    # R1 client to two different downstream modules (FOCOM, A1 Related),
    # proving the real fail-fast halt (a genuine downstream rejection
    # halts the order; the never-attempted step stays PENDING), then
    # cancel to turn the PENDING step CANCELLED.
    order = mesh["so-smos"].post("/orders", json={
        "scope": "demo-multi-step-order",
        "steps": [
            {"stepType": "INFRA", "targetModule": "FOCOM", "spec": {"resourceTypeId": "gpu-l40", "description": "SO SMOS provisioned node"}},
            {"stepType": "POLICY", "targetModule": "A1_RELATED", "policyTypeId": "NOT_A_REAL_POLICY_TYPE",
             "policyObject": {"scope": {"cellId": "demo-cell-1"}}, "nearRtRicId": "mock-near-rt-ric-001"},
            {"stepType": "TRAINING", "targetModule": "AI_ML_WORKFLOW", "producerId": "hello-world-rapp"},
        ],
    })
    assert order.status_code == 202
    order_id = order.json()["orderId"]
    steps = order.json()["steps"]
    assert steps[0]["status"] == "COMPLETED"
    assert steps[1]["status"] == "FAILED"
    assert steps[2]["status"] == "PENDING"

    order_status = mesh["so-smos"].get(f"/orders/{order_id}")
    assert order_status.json()["steps"][2]["status"] == "PENDING"

    cancelled = mesh["so-smos"].post(f"/orders/{order_id}/cancel")
    assert cancelled.status_code == 200
    cancelled_steps = cancelled.json()["steps"]
    assert cancelled_steps[0]["status"] == "COMPLETED"
    assert cancelled_steps[1]["status"] == "FAILED"
    assert cancelled_steps[2]["status"] == "CANCELLED"

    # step 17: DME type subscriptions — a real consumer notified when any
    # DmeType is registered or removed, closed in an earlier §5 pass but
    # never demonstrated. Intercepted the same way as FOCOM's/Policy
    # Mgmt's/A1 Related's/RAN Analytics' own notification steps above.
    dme_type_notifications = []
    real_post_dme = httpx.post

    def fake_post_dme(location, json=None, timeout=None, **kwargs):
        if location == "http://demo-consumer:9000/dme-type-events":
            dme_type_notifications.append(json)
            raise httpx.ConnectError("no real listener in this test, matching the runbook's own note")
        return real_post_dme(location, json=json, timeout=timeout, **kwargs)

    monkeypatch.setattr(loaded_apps["dme"].httpx, "post", fake_post_dme)

    dme_sub = mesh["dme"].post("/type-subscriptions", json={
        "notificationDestination": "http://demo-consumer:9000/dme-type-events", "owner": "hello-world-rapp",
    })
    assert dme_sub.status_code == 201
    dme_subscription_id = dme_sub.json()["subscriptionId"]

    new_type = mesh["dme"].post("/production-capabilities", json={
        "namespace": "demo", "name": "dme-type-sub-demo", "version": "1.0",
        "typeName": "dme-type-sub-demo-v1", "producerId": "hello-world-rapp",
        "dataProductionSchema": {"type": "object", "properties": {"reading": {"type": "number"}}},
        "producerHealthCallbackUrl": "http://hello-world-rapp:8080/health",
        "jobCallbackUrl": "http://hello-world-rapp:8080/dme-jobs",
    })
    assert new_type.status_code == 201
    new_type_id = new_type.json()["registrationId"]
    assert len(dme_type_notifications) == 1
    assert dme_type_notifications[0]["infoTypeId"] == new_type_id
    assert dme_type_notifications[0]["status"] == "REGISTERED"

    deregistered = mesh["dme"].delete("/production-capabilities", params={"producer_id": "hello-world-rapp"})
    assert deregistered.status_code == 204
    # tears down both hello-world-rapp's DmeTypes: step 4's hello-world-metrics
    # and the new demo one above, each firing its own DEREGISTERED notification.
    assert len(dme_type_notifications) == 3
    assert {n["status"] for n in dme_type_notifications[1:]} == {"DEREGISTERED"}

    dme_unsub = mesh["dme"].delete(f"/type-subscriptions/{dme_subscription_id}")
    assert dme_unsub.status_code == 204

    # step 18: retire — the real package priming lifecycle (COMMISSIONED-
    # equivalent AVAILABLE -> PRIMING -> PRIMED), a genuine deprime
    # refusal while the sample rApp's own instance is still deployed
    # (the reference's own deprimeRapp guard, a real query against
    # PackageUsageRegistration), terminate (which itself calls
    # Onboarding's real usage/stop), then deprime succeeding for real,
    # then terminate/delete.
    primed = mesh["onboarding"].post(f"/packages/{package_id}/prime")
    assert primed.status_code == 200
    assert primed.json()["state"] == "PRIMED"

    blocked_deprime = mesh["onboarding"].post(f"/packages/{package_id}/deprime")
    assert blocked_deprime.status_code == 409
    assert blocked_deprime.json()["detail"]["title"] == "SERVICE_NAME_CONFLICT"

    term = mesh["rapp-mgmt"].post(f"/instances/{instance_id}/terminate")
    assert term.status_code == 200
    assert term.json()["state"] == "UNDEPLOYED"

    deprimed = mesh["onboarding"].post(f"/packages/{package_id}/deprime")
    assert deprimed.status_code == 200
    assert deprimed.json()["state"] == "AVAILABLE"

    delete = mesh["rapp-mgmt"].delete(f"/instances/{instance_id}")
    assert delete.status_code == 204
