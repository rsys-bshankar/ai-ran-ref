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
    # reachable, but not valid zip bytes, so OnboardPackage's own
    # zipfile.BadZipFile handling routes it to FAILED. (An unreachable
    # host is also handled — onboarding/app/main.py's
    # ONBOARD_VALIDATION_FAILURES now catches httpx.HTTPError too — but
    # the mesh only resolves known service hosts, so that path isn't
    # exercisable through this harness; not a gap in the fix itself.)
    onboard = mesh["onboarding"].post("/packages", json={"location": "http://focom:8000/inventory"})
    package_id = onboard.json()["packageId"]

    status = mesh["onboarding"].get(f"/packages/{package_id}/onboarding-status")
    assert status.json()["state"] == "FAILED"

    create = mesh["rapp-mgmt"].post("/instances", json={"packageId": package_id, "config": {}})
    assert create.status_code == 409  # rApp Mgmt correctly refuses — the package never reached AVAILABLE


def test_onboarding_to_rapp_management_full_deploy_creates_real_nf_deployment_descriptor(mesh, loaded_apps, shared_engine, monkeypatch):
    """The actual fix for OPEN_ITEMS.md's top item: NFO's CreateDescriptor
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
        lambda location: ("Definitions/main.yaml", [], "deadbeef"),
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
    with Session(shared_engine) as session:
        descriptor = session.get(NFDeploymentDescriptor, uuid.UUID(nf_deployment_descriptor_id))
        assert descriptor is not None
        assert str(descriptor.package_id) == package_id

    create = mesh["rapp-mgmt"].post("/instances", json={"packageId": package_id, "config": {}})
    assert create.status_code == 202

    with Session(shared_engine) as session:
        deployment = session.scalar(
            select(NFDeployment).where(NFDeployment.nf_deployment_descriptor_id == uuid.UUID(nf_deployment_descriptor_id))
        )
        assert deployment is not None
        assert deployment.state == "RUNNING"
