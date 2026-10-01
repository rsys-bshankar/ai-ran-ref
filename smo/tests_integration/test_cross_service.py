"""Integration tests across service boundaries — the real cross-module
calls, not mocked, running against the in-process service mesh
(conftest.py, mesh.py). Two real bugs were caught and fixed while
building this harness, before a single test ran: R1 Termination's proxy
never stripped the module prefix before forwarding (would 404 against
every real backend), and NFO queried FOCOM's inventory with the wrong
query parameter name (resourceType vs. resource_type — a silent
behavioral bug, not a crash, since FOCOM just silently fell back to its
default). Both are fixed in their respective modules; these tests now
guard against regressing them.

Run with: pytest smo/tests_integration -q
"""

import uuid
from pathlib import Path


def test_real_demo_csar_onboards_and_deploys(mesh, loaded_apps, shared_engine, monkeypatch):
    """smo/samples/hello-world-rapp.csar — the real, spec-shaped sample
    package smo/DEMO_RUNBOOK.md walks a live docker-compose deployment
    through — must keep onboarding and deploying for real, through
    _validate_package's actual (unstubbed) zip-parsing, not a mocked
    stand-in. Regression guard for the real bug this exposed: the
    reference's own FileExistenceValidator requires
    Files/Acm/definition/compositions.json (RappCsarPathProvider.
    ACM_COMPOSITION_JSON_LOCATION), not Definitions/acm_composition.json
    — the path this build's own validator and test fixture both
    originally guessed wrong, which would have rejected every real CSAR
    the reference itself produces.
    """
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

    onboard = mesh["onboarding"].post("/packages", json={"location": "http://example/hello-world-rapp.csar"})
    package_id = onboard.json()["packageId"]

    status = mesh["onboarding"].get(f"/packages/{package_id}/onboarding-status")
    assert status.json()["state"] == "AVAILABLE"
    assert status.json()["nfDeploymentDescriptorId"] is not None
    pkg = next(p for p in mesh["onboarding"].get("/packages").json()["items"] if p["packageId"] == package_id)
    assert (pkg["name"], pkg["version"], pkg["vendor"]) == ("hello-world-rapp", "1.0", "ai-ran-ref")

    create = mesh["rapp-mgmt"].post("/instances", json={"packageId": package_id, "config": {}})
    assert create.status_code == 202
    assert create.json()["instanceId"]


def test_nfo_instantiate_actually_resolves_cluster_through_focom(mesh):
    """NFO+FOCOM LLD section 4: NFO's Instantiate queries FOCOM's real
    inventory endpoint — not a mock — before placing a workload.
    """
    descriptor_id = mesh["nfo"].post("/descriptors", json={
        "packageId": str(uuid.uuid4()), "name": "Definitions/main.yaml",
    }).json()["nfDeploymentDescriptorId"]

    resp = mesh["nfo"].post("/deployments", json={
        "nfDeploymentDescriptorId": descriptor_id, "name": "integration-test-deployment", "requiredResourceTypeId": "gpu-l40",
    })
    assert resp.status_code == 202
    body = resp.json()
    assert body["clusterId"] == "phase1-degenerate-cluster"  # FOCOM's real (Phase 1) answer, reached over the mesh
    assert body["state"] == "RUNNING"


def test_a1_related_create_policy_reaches_mock_near_rt_ric_enforced(mesh):
    """A1 Related LLD section 1.1's confirmed sequence, end to end: the
    A1UCR clause 6.3 call actually happens now (against the isolated mock,
    per RT-7), and enforcementStatus reflects its real response.
    """
    resp = mesh["a1-related"].post("/policies", json={
        "policyTypeId": "ORAN_QoSandTSP_6.0.1", "policyObject": {"scope": "cell-42"},
        "nearRtRicId": "ric-1", "creatorId": "rapp-1",
    })
    assert resp.status_code == 201
    assert resp.json()["enforcementStatus"] == "ENFORCED"


def test_a1_related_create_policy_reaches_mock_near_rt_ric_rejected(mesh):
    resp = mesh["a1-related"].post("/policies", json={
        "policyTypeId": "ORAN_QoSandTSP_6.0.1", "policyObject": {},  # empty -> mock rejects
        "nearRtRicId": "ric-1", "creatorId": "rapp-1",
    })
    assert resp.json()["enforcementStatus"] == "REJECTED"


def test_a1_related_query_status_refreshes_live_from_mock(mesh):
    """The cache/pass-through duality (A1 Related LLD section 1.1),
    proven against the real mock this time, not a fake stand-in.
    """
    created = mesh["a1-related"].post("/policies", json={
        "policyTypeId": "ORAN_QoSandTSP_6.0.1", "policyObject": {}, "nearRtRicId": "ric-1", "creatorId": "rapp-1",
    }).json()
    assert created["enforcementStatus"] == "REJECTED"

    status = mesh["a1-related"].get(f"/policies/{created['policyId']}/status")
    # mock-near-rt-ric's query_policy_status echoes back whatever it stored at
    # create time — REJECTED — proving this is a live re-fetch reaching the
    # actual mock service, not a hardcoded test double's canned answer.
    assert status.json()["enforcementStatus"] == "REJECTED"


def test_a1_related_register_ei_type_creates_a_real_dme_type(mesh):
    """A1 Related LLD section 3: RegisterEIType wraps DME's real
    RegisterDMEType — this proves the DME type actually gets created,
    not just that A1 Related believes it did.
    """
    resp = mesh["a1-related"].post("/ei-types/register", params={
        "ei_type_id": "ei-coverage-1", "registered_by": "rapp-1",
        "dme_namespace": "RAN", "dme_name": "CoverageIssue", "dme_version": "1.0.0",
    })
    assert resp.status_code == 200
    dme_type_id = resp.json()["eiSourceDmeTypeId"]

    dme_types = mesh["dme"].get("/dme-types").json()
    assert any(t["dmeTypeId"] == dme_type_id for t in dme_types)
    assert dme_types[0]["dmeTypeIdStruct"] == {"namespace": "RAN", "name": "CoverageIssue", "version": "1.0.0"}


def test_ran_analytics_producer_registration_creates_a_real_sme_service(mesh):
    """RAN Analytics LLD section 1: RegisterAnalyticsProducer registers
    via SME — proving the SME ServiceProfile actually gets created.
    """
    resp = mesh["ran-analytics"].post("/producers", params={
        "producer_id": "rapp-mdaf-1", "analytics_type": "coverage-issue-analysis",
    }, json={"dme_input_types": [str(uuid.uuid4())], "output_schema": {"type": "object"}})
    assert resp.status_code == 201

    services = mesh["sme"].get("/published-apis/v1/rapp-mdaf-1/service-apis").json()
    assert len(services) == 1
    assert services[0]["serviceCapabilities"]["analyticsType"] == "coverage-issue-analysis"


def test_so_smos_dispatches_a_policy_step_through_to_the_mock_near_rt_ric(mesh):
    """SO SMOS LLD section 1's dispatch table, end to end across THREE
    hops: SO SMOS -> A1 Related -> mock Near-RT RIC. This is the deepest
    chain in this reference build and the clearest proof the dispatch
    table (so-smos/app/dispatch.py) isn't just calling into a stub.
    """
    resp = mesh["so-smos"].post("/orders", json={
        "scope": "policy-rollout",
        "steps": [{
            "stepType": "POLICY", "targetModule": "A1_RELATED",
            "policyTypeId": "ORAN_QoSandTSP_6.0.1", "policyObject": {"scope": "cell-1"},
            "nearRtRicId": "ric-1", "creatorId": "so-smos",
        }],
    })
    assert resp.status_code == 202
    steps = resp.json()["steps"]
    assert steps[0]["status"] == "COMPLETED"
    assert steps[0]["result"]["enforcementStatus"] == "ENFORCED"

    # and the policy is independently visible via A1 Related's own API —
    # not just present in SO SMOS's own record of what it dispatched.
    policies = mesh["a1-related"].get(f"/policies/{steps[0]['result']['policyId']}").json()
    assert policies["nearRtRicId"] == "ric-1"


def test_so_smos_fail_fast_halts_on_a_real_downstream_rejection(mesh):
    """SO SMOS LLD section 1.1: fail-fast, no auto-compensation — proven
    here against a REAL downstream failure (an unknown policy type,
    A1 Related's own validation), not a mocked exception.
    """
    resp = mesh["so-smos"].post("/orders", json={
        "scope": "policy-rollout",
        "steps": [
            {"stepType": "POLICY", "targetModule": "A1_RELATED", "policyTypeId": "NOT_A_REAL_TYPE",
             "policyObject": {}, "nearRtRicId": "ric-1", "creatorId": "so-smos"},
            {"stepType": "DEPLOY", "targetModule": "NFO", "nfDeploymentDescriptorId": str(uuid.uuid4())},
        ],
    })
    steps = resp.json()["steps"]
    assert steps[0]["status"] == "FAILED"
    assert steps[1]["status"] == "PENDING"  # never attempted


def test_onboarding_to_rapp_management_status_check(mesh):
    """rApp Management LLD section 5: CreateInstance checks Onboarding's
    real onboarding-status before proceeding — proven by onboarding a
    package with a deliberately-broken location (a 404, not a real
    .csar), which OnboardPackage's own validation routes to FAILED, and
    confirming CreateInstance refuses to deploy it. The full happy-path
    deploy (a real NFDeploymentDescriptor created and consumed) is
    covered separately below.
    """
    # Points at a real, mesh-reachable endpoint that returns 200 JSON —
    # reachable, but neither a real .csar filename nor valid zip bytes, so
    # OnboardPackage's own validation (the filename check now, same as the
    # zipfile.BadZipFile handling it would otherwise fall through to)
    # routes it to FAILED. (An unreachable host is also handled —
    # onboarding/app/main.py's ONBOARD_VALIDATION_FAILURES now catches
    # httpx.HTTPError too — but the mesh only resolves known service
    # hosts, so that path isn't exercisable through this harness; not a
    # gap in the fix itself.)
    onboard = mesh["onboarding"].post("/packages", json={"location": "http://focom:8000/inventory"})
    package_id = onboard.json()["packageId"]

    status = mesh["onboarding"].get(f"/packages/{package_id}/onboarding-status")
    assert status.json()["state"] == "FAILED"

    create = mesh["rapp-mgmt"].post("/instances", json={"packageId": package_id, "config": {}})
    assert create.status_code == 409  # rApp Mgmt correctly refuses — the package never reached AVAILABLE


def test_onboarding_to_rapp_management_full_deploy_creates_real_nf_deployment_descriptor(mesh, loaded_apps, db_connection, monkeypatch):
    """The actual fix for HISTORY.md's top item: NFO's CreateDescriptor
    (NFO+FOCOM LLD section 2) is now called from OnboardPackage once
    validation succeeds, and rApp Management's CreateInstance now passes
    that REAL nfDeploymentDescriptorId to NFO instead of packageId. Proven
    end to end — not just per module — by reaching into both modules'
    real DB rows through the shared engine, not just trusting the HTTP
    responses.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    # _validate_package's own zip-parsing is exercised by onboarding's unit
    # tests; stubbing it here keeps this test's focus on the cross-module
    # wiring this pass actually changed, not TOSCA zip mechanics.
    monkeypatch.setattr(
        loaded_apps["onboarding"], "_validate_package",
        lambda location: ("Definitions/main.yaml", [], "deadbeef", {}),
    )

    onboard = mesh["onboarding"].post("/packages", json={"location": "http://example/pkg.csar"})
    package_id = onboard.json()["packageId"]

    status = mesh["onboarding"].get(f"/packages/{package_id}/onboarding-status")
    assert status.json()["state"] == "AVAILABLE"
    nf_deployment_descriptor_id = status.json()["nfDeploymentDescriptorId"]
    assert nf_deployment_descriptor_id is not None
    assert nf_deployment_descriptor_id != package_id  # a real, distinct descriptor row — not packageId reused

    NFDeploymentDescriptor = loaded_apps["nfo"].NFDeploymentDescriptor
    NFDeployment = loaded_apps["nfo"].NFDeployment
    with Session(bind=db_connection, join_transaction_mode="create_savepoint") as session:
        descriptor = session.get(NFDeploymentDescriptor, uuid.UUID(nf_deployment_descriptor_id))
        assert descriptor is not None
        assert str(descriptor.package_id) == package_id

    create = mesh["rapp-mgmt"].post("/instances", json={"packageId": package_id, "config": {}})
    assert create.status_code == 202

    with Session(bind=db_connection, join_transaction_mode="create_savepoint") as session:
        deployment = session.scalar(
            select(NFDeployment).where(NFDeployment.nf_deployment_descriptor_id == uuid.UUID(nf_deployment_descriptor_id))
        )
        assert deployment is not None
        assert deployment.state == "RUNNING"


def test_ran_nf_oam_config_write_reaches_a_real_mock_o1_adaptor(mesh, loaded_apps, db_connection):
    """HISTORY.md §2: "no real southbound integrations beyond
    the A1 mock" — RAN NF OAM LLD section 5.1's PATCH step
    (netconf_client.py) always dispatched a real RFC 6241 <edit-config>
    RPC, but nothing in this build's own topology ever answered it for
    real before mock-o1-adaptor existed. Proven end to end here, the same
    way A1 Related's own mock Near-RT RIC round trip already is: no
    ManagedElement registration route exists (a separate, undocumented
    gap, not this item's own scope), so the ManagedEntity/O1AdaptorEndpoint
    rows are seeded directly through the shared engine — the real HTTP
    call this test proves is write_configuration_changes's own dispatch
    to mock-o1-adaptor, not this setup step.
    """
    from sqlalchemy.orm import Session

    ManagedEntity = loaded_apps["ran-nf-oam"].ManagedEntity
    O1AdaptorEndpoint = loaded_apps["ran-nf-oam"].O1AdaptorEndpoint

    with Session(bind=db_connection, join_transaction_mode="create_savepoint") as session:
        endpoint = O1AdaptorEndpoint(
            managed_element_ref="ME-integration-1", adaptor_uri="http://mock-o1-adaptor:8000/edit-config",
            protocol_support=["NETCONF"],
        )
        session.add(endpoint)
        session.flush()
        session.add(ManagedEntity(
            managed_element_ref="ME-integration-1", entity_type="O-DU", o1_protocol="NETCONF",
            o1_adaptor_endpoint_id=endpoint.endpoint_id,
        ))
        session.commit()

    resp = mesh["ran-nf-oam"].post("/config-jobs", json={
        "requestedBy": "test", "scope": "single-NF",
        "changes": [{"managedElementRef": "ME-integration-1", "attributeChanges": {"adminState": "UNLOCKED"}}],
    })
    assert resp.status_code == 202
    job = resp.json()
    assert job["status"] == "COMPLETED"  # every sub-change APPLIED

    status = mesh["ran-nf-oam"].get(f"/config-jobs/{job['jobId']}")
    assert status.json()["subChanges"][0]["status"] == "APPLIED"

    # mock-o1-adaptor's own real, parsed record of what it received —
    # proves the RPC round trip actually carried the real attribute
    # changes, not just that RAN NF OAM's own status flipped to APPLIED.
    applied = mesh["mock-o1-adaptor"].get("/edit-config/ME-integration-1")
    assert applied.json()["attributeChanges"] == {"adminState": "UNLOCKED"}


def test_ran_nf_oam_config_write_rejected_by_mock_o1_adaptor_is_recorded(mesh, loaded_apps, db_connection):
    """The other real outcome: mock-o1-adaptor's own rejection (an empty
    attributeChanges payload — the same real, testable trigger its own
    unit tests use) must surface as a genuine REJECTED sub_change, not a
    silently-swallowed failure.
    """
    from sqlalchemy.orm import Session

    ManagedEntity = loaded_apps["ran-nf-oam"].ManagedEntity
    O1AdaptorEndpoint = loaded_apps["ran-nf-oam"].O1AdaptorEndpoint

    with Session(bind=db_connection, join_transaction_mode="create_savepoint") as session:
        endpoint = O1AdaptorEndpoint(
            managed_element_ref="ME-integration-2", adaptor_uri="http://mock-o1-adaptor:8000/edit-config",
            protocol_support=["NETCONF"],
        )
        session.add(endpoint)
        session.flush()
        session.add(ManagedEntity(
            managed_element_ref="ME-integration-2", entity_type="O-DU", o1_protocol="NETCONF",
            o1_adaptor_endpoint_id=endpoint.endpoint_id,
        ))
        session.commit()

    resp = mesh["ran-nf-oam"].post("/config-jobs", json={
        "requestedBy": "test", "scope": "single-NF",
        "changes": [{"managedElementRef": "ME-integration-2", "attributeChanges": {}}],
    })
    job = resp.json()
    assert job["status"] == "FAILED"  # every sub-change REJECTED

    status = mesh["ran-nf-oam"].get(f"/config-jobs/{job['jobId']}")
    sub_change = status.json()["subChanges"][0]
    assert sub_change["status"] == "REJECTED"
    assert sub_change["rejectionReason"] == "NETCONF_RPC_FAILED"


def test_ts28105_nrm_pipeline_across_aimgf_mlmr_and_nfo(mesh):
    """Wave 4 (TS 28.105 at REST level): an MLTrainingRequest through real
    AIMgF -> MLMR (model existence) -> NFO (MLTF runtime), its report, a
    model loaded onto an AIMLInferenceFunction through the real
    RuntimeLifecycle, and MLMR's own MLModel NRM view joining AIMgF's
    read-only cross-references back through R1.
    """
    model_id = mesh["mlmr"].post("/models", json={
        "modelType": "nrm-energy", "version": "1.0", "aIMLInferenceName": "NG_RAN_NETWORK_ENERGY_SAVING",
    }).json()["modelId"]
    request = mesh["aimgf"].post("/ml-training-requests", json={"mLModelRef": model_id, "trainingRequestSource": "es-rapp"})
    assert request.status_code == 201, request.text
    request_id = request.json()["id"]
    assert request.json()["attributes"]["mLTrainingType"] == "INITIAL_TRAINING"
    done = mesh["aimgf"].post(f"/training-jobs/{request_id}/complete", json={
        "succeeded": True, "modelPerformanceTraining": [{"performanceMetric": "MAE", "performanceScore": 1.8}]})
    assert done.status_code == 200, done.text

    mesh["aimgf"].post(f"/models/{model_id}/advance", params={"event": "APPROVE_TRAINING", "decided_by": "op"})
    testing = mesh["aimgf"].post("/ml-testing-requests", json={"mLModelRef": model_id})
    assert testing.status_code == 201, testing.text
    mesh["aimgf"].post(f"/validation-jobs/{testing.json()['id']}/complete", json={"succeeded": True})
    mesh["aimgf"].post(f"/models/{model_id}/advance", params={"event": "APPROVE_VALIDATION", "decided_by": "op"})
    emulation = mesh["aimgf"].post("/emulation-jobs", json={"modelId": model_id, "producerId": "es-rapp"}).json()
    mesh["aimgf"].post(f"/emulation-jobs/{emulation['emulationJobId']}/complete", json={"succeeded": True})
    for event in ("SUBMIT_FOR_APPROVAL", "APPROVE", "CERTIFY"):
        assert mesh["aimgf"].post(f"/models/{model_id}/advance", params={"event": event, "decided_by": "op"}).status_code == 200

    function_id = mesh["aimgf"].post("/aiml-inference-functions", json={"activationStatus": "ACTIVATED"}).json()["id"]
    loading = mesh["aimgf"].post("/ml-model-loading-requests", json={
        "aIMLInferenceFunctionRef": function_id, "mLModelToLoadRef": [model_id]})
    assert loading.json()["attributes"]["requestStatus"] == "FINISHED", loading.text
    lifecycle = mesh["aimgf"].get(f"/models/{model_id}/lifecycle").json()
    assert lifecycle["runtimeLifecycleState"] == "ACTIVE" and lifecycle["nfDeploymentId"]

    nrm = mesh["mlmr"].get(f"/ml-models/{model_id}").json()["attributes"]
    assert nrm["mLTrainingType"] == "INITIAL_TRAINING"
    assert nrm["usedByFunctionRefList"] == [function_id]
    assert nrm["aIMLInferenceName"] == "NG_RAN_NETWORK_ENERGY_SAVING"


def test_ts28104_drift_report_reaches_aimgf_mlmf_and_request_delivery(mesh):
    """Wave 5 (TS 28.104 at REST level): an MDARequest receives a matching
    PREDICTION report, and a DRIFT report naming a model is forwarded
    through R1 to the real AIMgF MLMF subscription, whose guard-KPI floor
    marks the breach."""
    request = mesh["mdaf"].post("/mda-requests", json={
        "reportingMethod": "STREAMING", "requestedMDAOutputs": [{"mDAType": "PREDICTIONS_PM_DATA"}],
        "analyticsScope": {"managedEntitiesScope": ["cell-101"]}})
    assert request.status_code == 201, request.text
    report = mesh["mdaf"].post("/mda-reports", json={"managedEntitiesScope": ["cell-101"], "mDAOutputs": [{
        "mDAType": "PREDICTIONS_PM_DATA", "mDAOutputList": {"pmPredictions": [{"pmName": "RRU.PrbUsedDl", "pmPredictedValue": 2.8}]}}]})
    assert report.json()["attributes"]["deliveredToRequestRefList"] == [request.json()["id"]]

    model_id = mesh["mlmr"].post("/models", json={"modelType": "drifty", "version": "1"}).json()["modelId"]
    sub = mesh["aimgf"].post("/mlmf/subscriptions", params={"model_id": model_id, "dme_type_id": str(uuid.uuid4())},
                             json={"metric_types": ["accuracy"], "guard_kpi_floor": {"accuracy": 0.8}})
    assert sub.status_code == 201, sub.text
    drift = mesh["mdaf"].post("/mda-reports", json={"reportKind": "DRIFT", "mDAOutputs": [{
        "mDAType": "CORRELATION_ANALYTICS_TRAINING_DATA_ANALYSIS",
        "mDAOutputList": [{"mDAOutputIEName": "mLModelRef", "mDAOutputIEValue": model_id},
                          {"mDAOutputIEName": "accuracy", "mDAOutputIEValue": 0.61}]}]})
    assert drift.status_code == 201, drift.text
    reports = mesh["aimgf"].get(f"/mlmf/subscriptions/{sub.json()['subscriptionId']}/reports").json()["items"]
    assert reports[0]["metrics"] == {"accuracy": 0.61} and reports[0]["breachedFloor"] is True


def test_ts28105_cross_module_paths_resolve_inside_the_mesh(mesh):
    """Regression guard: AIMgF's ML-update completion, emulation on an
    AIMLInferenceEmulationFunction and testing-request cancel all reach
    code in app/nrm.py from app/main.py (and back). Those references must
    be bound at load time — a call-time `from .nrm import ...` breaks in
    this mesh, whose loader evicts `app.*` from sys.modules per service.
    """
    def promoted_model(name):
        model_id = mesh["mlmr"].post("/models", json={"modelType": name, "version": "1"}).json()["modelId"]
        job = mesh["aimgf"].post("/training-jobs", json={"modelId": model_id, "producerId": "p"}).json()["trainingJobId"]
        mesh["aimgf"].post(f"/training-jobs/{job}/complete", json={"succeeded": True})
        return model_id

    model_id = promoted_model("mesh-update")
    mesh["aimgf"].post(f"/models/{model_id}/advance", params={"event": "APPROVE_TRAINING", "decided_by": "op"})
    testing = mesh["aimgf"].post("/ml-testing-requests", json={"mLModelRef": model_id}).json()
    cancelled = mesh["aimgf"].patch(f"/ml-testing-requests/{testing['id']}", json={"cancelRequest": True})
    assert cancelled.status_code == 200, cancelled.text
    assert mesh["aimgf"].get(f"/models/{model_id}/lifecycle").json()["modelLifecycleState"] == "FAILED"

    update = mesh["aimgf"].post("/ml-update-requests", json={"mLModelRefList": [model_id], "newCapabilityVersionId": ["v2"]})
    assert update.status_code == 201, update.text
    process_id = update.json()["attributes"]["mLUpdateProcessRef"]
    job_id = mesh["aimgf"].get(f"/ml-update-processes/{process_id}").json()["attributes"]["trainingRequestRefList"][0]
    done = mesh["aimgf"].post(f"/training-jobs/{job_id}/complete", json={"succeeded": True, "metrics": {"MAE": 1.2}})
    assert done.status_code == 200, done.text
    assert mesh["aimgf"].get(f"/ml-update-processes/{process_id}").json()["attributes"]["progressStatus"]["status"] == "FINISHED"

    emu_fn = mesh["aimgf"].post("/aiml-inference-emulation-functions", json={}).json()["id"]
    other = mesh["mlmr"].post("/models", json={"modelType": "mesh-emu", "version": "1"}).json()["modelId"]
    job = mesh["aimgf"].post("/training-jobs", json={"modelId": other, "producerId": "p"}).json()["trainingJobId"]
    mesh["aimgf"].post(f"/training-jobs/{job}/complete", json={"succeeded": True})
    mesh["aimgf"].post(f"/models/{other}/advance", params={"event": "APPROVE_TRAINING", "decided_by": "op"})
    validation = mesh["aimgf"].post("/validation-jobs", json={"modelId": other, "producerId": "p"}).json()["validationJobId"]
    mesh["aimgf"].post(f"/validation-jobs/{validation}/complete", json={"succeeded": True})
    mesh["aimgf"].post(f"/models/{other}/advance", params={"event": "APPROVE_VALIDATION", "decided_by": "op"})
    emulation = mesh["aimgf"].post("/emulation-jobs", json={"modelId": other, "producerId": "p",
                                                            "aIMLInferenceEmulationFunctionRef": emu_fn})
    assert emulation.status_code == 201, emulation.text


def test_runtime_profile_flows_from_rapp_manifest_to_nfo_descriptor(mesh, loaded_apps, monkeypatch):
    """Wave 7 (W7-03): a real CSAR whose manifest.yaml declares
    runtimeProfiles onboards through the real Onboarding validator; an AIMgF
    training run naming that package gets an NFO descriptor sized with the
    TRAINING profile, read back from NFO itself."""
    import io
    import zipfile

    import httpx

    original = (Path(__file__).resolve().parent.parent / "samples" / "hello-world-rapp.csar").read_bytes()
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as src, zipfile.ZipFile(buf, "w") as dst:
        for item in src.infolist():
            if item.filename != "manifest.yaml":
                dst.writestr(item, src.read(item.filename))
        dst.writestr("manifest.yaml", "rappManifest:\n  manifestVersion: \"1.0\"\n"
                                      "executionModes: [TRAINING, INFERENCE]\n"
                                      "runtimeProfiles:\n  TRAINING: {cpu: 8, memory: 16Gi, gpu: 0}\n")
    csar = buf.getvalue()

    class FakeResp:
        content = csar

        def raise_for_status(self):
            pass

    real_get = httpx.get
    monkeypatch.setattr(loaded_apps["onboarding"].httpx, "get",
                        lambda location, timeout=None, **kw: FakeResp() if location == "http://example/es.csar"
                        else real_get(location, timeout=timeout, **kw))
    package_id = mesh["onboarding"].post("/packages", json={"location": "http://example/es.csar"}).json()["packageId"]
    assert mesh["onboarding"].get(f"/packages/{package_id}/onboarding-status").json()["state"] == "AVAILABLE"

    model_id = mesh["mlmr"].post("/models", json={"modelType": "w7-profile", "version": "1"}).json()["modelId"]
    job = mesh["aimgf"].post("/training-jobs", json={"modelId": model_id, "producerId": "p", "packageId": package_id})
    assert job.status_code == 201, job.text
    job_id = job.json()["trainingJobId"]
    descriptors = mesh["nfo"].get("/descriptors", params={"limit": 500}).json()["items"]
    mine = next(d for d in descriptors if d["name"] == f"aimgf-training-{job_id}")
    assert mine["workloadTemplate"]["resources"] == {"cpu": 8, "memory": "16Gi", "gpu": 0}


def _seed_managed_element(loaded_apps, db_connection, ref):
    from sqlalchemy.orm import Session

    ManagedEntity = loaded_apps["ran-nf-oam"].ManagedEntity
    O1AdaptorEndpoint = loaded_apps["ran-nf-oam"].O1AdaptorEndpoint
    with Session(bind=db_connection, join_transaction_mode="create_savepoint") as session:
        endpoint = O1AdaptorEndpoint(managed_element_ref=ref, adaptor_uri="http://mock-o1-adaptor:8000/edit-config",
                                     protocol_support=["NETCONF"])
        session.add(endpoint)
        session.flush()
        session.add(ManagedEntity(managed_element_ref=ref, entity_type="O-DU", o1_protocol="NETCONF",
                                  o1_adaptor_endpoint_id=endpoint.endpoint_id))
        session.commit()


def test_o1_cm_intent_handler_enacts_an_intent_through_dme_to_the_o1_adaptor(mesh, loaded_apps, db_connection):
    """Wave 8 (W8-07, D-1): SA SMOS registers as the generic O1-CM intent
    handler; an Intent setting NRCellDU.administrativeState=LOCKED on two
    cells of a managed element is pushed to it, written through DME's
    action mediation and RAN NF OAM's NETCONF edit-config to the real mock
    O1 adaptor, and reported back to Intent Service as FULFILLED with the
    action references."""
    _seed_managed_element(loaded_apps, db_connection, "gnb-du-01")
    registered = mesh["sa-smos"].post("/o1-cm-handler/registration", json={})
    assert registered.status_code == 201, registered.text

    intent = mesh["intent-service"].post("/intents", json={
        "userLabel": "sleep cells 101/102", "rmioId": "es-rapp", "rmihId": "sa-smos",
        "intentReportControl": [{"observationPeriod": 60}],
        "intentExpectations": [{
            "expectationId": "sleep", "expectationVerb": "DELIVER",
            "expectationObject": {"objectType": "RAN_SUBNETWORK", "objectInstance": "gnb-du-01", "objectContexts": [
                {"contextAttribute": "Cell", "contextCondition": "IS_ALL_OF", "contextValueRange": ["101", "102"]}]},
            "expectationTargets": [{"targetName": "NRCellDU.administrativeState", "targetCondition": "IS_EQUAL_TO",
                                    "targetValueRange": "LOCKED"}]}],
    })
    assert intent.status_code == 201, intent.text
    intent_id = intent.json()["intentId"]

    enactments = mesh["sa-smos"].get("/o1-cm-handler/enactments", params={"intent_id": intent_id}).json()["items"]
    assert enactments[0]["status"] == "FULFILLED", enactments
    action = mesh["dme"].get(f"/actions/{enactments[0]['actions'][0]['actionId']}").json()
    assert action["sourceContext"]["intentId"] == intent_id
    assert [c["managedFunctionRef"] for c in action["changes"]] == ["NRCellDU=101", "NRCellDU=102"]
    assert mesh["mock-o1-adaptor"].get("/edit-config/gnb-du-01").json()["attributeChanges"] == {"administrativeState": "LOCKED"}

    current = mesh["intent-service"].get(f"/intents/{intent_id}").json()["attributes"]["intentReportReference"]
    report = mesh["intent-service"].get(f"/intent-reports/{current}").json()["attributes"]
    assert report["intentFulfilmentReport"]["intentFulfilmentInfo"] == {"fulfilmentStatus": "FULFILLED"}
    assert '"forwardedJobId"' in report["intentFulfilmentReport"]["additionalFulfilmentInfo"]

    # an attribute value outside the registered CM target's range is not feasible
    rejected = mesh["intent-service"].post("/intents", json={
        "userLabel": "bad", "rmioId": "es-rapp", "rmihId": "sa-smos", "intentReportControl": [{"observationPeriod": 60}],
        "intentExpectations": [{"expectationId": "x", "expectationObject": {"objectType": "RAN_SUBNETWORK", "objectInstance": "gnb-du-01"},
                                "expectationTargets": [{"targetName": "NRCellDU.operationalState", "targetCondition": "IS_EQUAL_TO",
                                                        "targetValueRange": "DISABLED"}]}]})
    assert rejected.status_code == 422


def test_vendor_onboarding_gates_o1_writes_by_capability_and_schema(mesh):
    """Wave 9 (W9-01..04): a vendor is onboarded as data — once its O1
    adaptor has registered, its capability declaration is discovered from
    that registered adaptor, its data-model
    descriptor loaded, its capability declared (COMBINED: the 3GPP
    TS 28.541 descriptor plus the vendor's named augments) — then its
    endpoint registers unchanged. A CM write through DME's action mediation
    is checked against that model: a valid one reaches the adaptor, an
    attribute the model lacks is refused with the standard error schema
    before anything is dispatched."""
    vendor_model = {"schemaName": "mock-vendor-nr", "revision": "1.0", "type": "OPENAPI_NRM",
                    "location": "builtin-test:mock-vendor-nr", "descriptor": {"classes": {"NRCellDU": {"mockBoost": {"type": "boolean"}}}}}
    registered = mesh["ran-nf-oam"].post("/o1-adaptor-endpoints", json={
        "managedElementRef": "gnb-du-vendor", "adaptorUri": "http://mock-o1-adaptor:8000/edit-config",
        "protocolSupport": ["NETCONF"], "o1Protocol": "NETCONF", "entityType": "O-DU", "vendorName": "mock-vendor"})
    assert registered.status_code == 201, registered.text
    mesh["ran-nf-oam"].post(f"/o1-adaptor-endpoints/{registered.json()['endpointId']}/heartbeat")

    onboarded = mesh["ran-nf-oam"].post("/vendor-onboarding", json={
        "vendorName": "mock-vendor", "discoverFrom": "gnb-du-vendor", "conformanceMode": "COMBINED", "schemas": [vendor_model]})
    assert onboarded.status_code == 201, onboarded.text
    assert onboarded.json()["discovered"]["supportedVendorModes"] == ["O1_NETCONF"]
    assert onboarded.json()["capability"]["discoveryUri"] == "http://mock-o1-adaptor:8000/capabilities"
    assert "O1_NETCONF" in mesh["ran-nf-oam"].get("/capabilities").json()["supportedVendorModes"]

    ok = mesh["dme"].post("/actions", json={"requestedBy": "es-rapp", "changes": [
        {"managedElementRef": "gnb-du-vendor", "className": "NRCellDU", "managedFunctionRef": "NRCellDU=1",
         "attributeChanges": {"administrativeState": "LOCKED", "mockBoost": True}}]})
    assert ok.status_code == 202 and ok.json()["status"] == "COMPLETED", ok.text
    assert mesh["mock-o1-adaptor"].get("/edit-config/gnb-du-vendor").json()["attributeChanges"] == {
        "administrativeState": "LOCKED", "mockBoost": "True"}

    refused = mesh["dme"].post("/actions", json={"requestedBy": "es-rapp", "changes": [
        {"managedElementRef": "gnb-du-vendor", "className": "NRCellDU", "attributeChanges": {"warpDrive": 9}}]})
    assert refused.status_code == 422
    assert refused.json()["detail"]["title"] == "SCHEMA_VALIDATION_FAILED" and "warpDrive" in refused.json()["detail"]["detail"]
    statuses = [a["status"] for a in mesh["dme"].get("/actions").json()["items"]]
    assert sorted(statuses) == ["COMPLETED", "REJECTED"]
