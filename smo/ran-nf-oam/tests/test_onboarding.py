"""PR-MGT-14: zero-touch onboarding. Templates (14.1), a registration selects one (14.2), applying it is a config job (14.3), the software baseline check (14.4) and the
status FSM (14.5). Everything is opt in: with no template defined a registration is what it always was."""

import json

import pytest
from sqlalchemy import select

from smo_shared.scope import SCOPE_HEADER

from test_main import client, db_session_factory  # noqa: F401  (pytest fixtures)

from app import lifecycle
from app.models import Alarm, ElementOnboarding
from app.netconf_client import EditResult
from app.statemachine import ONBOARDING_FSM, OnboardingEvent, OnboardingState
from smo_shared.statemachine import IllegalTransition

TEMPLATE = {"entityType": "O-DU", "changes": [{"managedFunctionRef": "NRCellDU=1", "attributeChanges": {"txPower": 20}},
                                             {"attributeChanges": {"adminState": "unlocked"}}]}


@pytest.fixture
def nf(monkeypatch):
    """A fake NF behind every registered element: `edits` is what was written, `fail` makes the write refused."""
    state = {"edits": [], "fail": False}

    def edit(adaptor_uri, target_ref, attribute_changes, message_id, operation="merge", managed_function_ref=None):
        if state["fail"]:
            return EditResult(False, "NETCONF_RPC_FAILED", "refused")
        state["edits"].append((target_ref, managed_function_ref, dict(attribute_changes)))
        return EditResult(True)

    monkeypatch.setattr("app.main.send_edit_config", edit)
    return state


def _template(client, name="du-basic", **over):
    resp = client.put(f"/onboarding-templates/{name}", json={**TEMPLATE, **over})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _register(client, ref="ME-1", **over):
    body = {"managedElementRef": ref, "adaptorUri": "http://adaptor:9000/netconf", "protocolSupport": ["NETCONF"], "o1Protocol": "NETCONF", "entityType": "O-DU", **over}
    resp = client.post("/o1-adaptor-endpoints", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _apply(client, ref="ME-1", **body):
    return client.post(f"/element-onboarding/{ref}/apply", json={"requestedBy": "alice", **body})


def _row(client, ref="ME-1"):
    return client.get(f"/element-onboarding/{ref}").json()


def _alarms(db_session_factory, ref="ME-1"):
    with db_session_factory() as db:
        return [(a.source_alarm_id, a.severity, a.probable_cause) for a in db.scalars(select(Alarm).where(Alarm.managed_element_ref == ref)).all()]


# ---- nothing changes until a template exists

def test_with_no_template_a_registration_is_what_it_was(client, db_session_factory):
    """With no onboarding template defined, registering an element adds no onboarding row or answer member, and the first heartbeat just makes the
    endpoint ACTIVE.
    """
    resp = _register(client, softwareVersion="1.0")
    assert "onboarding" not in resp
    assert client.get("/element-onboarding").json()["items"] == []
    assert client.get("/element-onboarding/ME-1").status_code == 404
    assert client.get("/element-onboarding/ME-1").json()["detail"]["title"] == "ELEMENT_ONBOARDING_NOT_FOUND"
    assert client.post("/o1-adaptor-endpoints/" + resp["endpointId"] + "/heartbeat").json()["healthStatus"] == "ACTIVE"


def test_a_disabled_template_does_not_count_as_a_template(client):
    """A disabled template does not start onboarding for a new element."""
    _template(client, enabled=False)
    assert "onboarding" not in _register(client)


# ---- MGT-14.1 the template store

def test_a_template_is_defined_read_listed_replaced_and_deleted(client):
    """A template can be defined, read, listed (also by entity type), replaced whole by a PUT (keeping its creation time) and deleted; a second
    delete is 404.
    """
    made = _template(client, description="basic DU", softwareBaseline="2.1", requireBaseline=True, autoApply=True)
    assert made["name"] == "du-basic" and made["entityType"] == "O-DU" and made["softwareBaseline"] == "2.1" and made["requireBaseline"] and made["autoApply"]
    assert made["changes"][0] == {"managedFunctionRef": "NRCellDU=1", "attributeChanges": {"txPower": 20}, "operation": "merge"}
    assert client.get("/onboarding-templates/du-basic").json() == made
    _template(client, "cu-basic", entityType="O-CU")
    assert {t["name"] for t in client.get("/onboarding-templates").json()["items"]} == {"du-basic", "cu-basic"}
    assert [t["name"] for t in client.get("/onboarding-templates", params={"entity_type": "O-CU"}).json()["items"]] == ["cu-basic"]
    replaced = _template(client, entityType="O-RU")                        # a PUT replaces it whole
    assert replaced["entityType"] == "O-RU" and replaced["createdAt"] == made["createdAt"] and replaced["autoApply"] is False
    assert client.delete("/onboarding-templates/du-basic").status_code == 204
    assert client.get("/onboarding-templates/du-basic").json()["detail"]["title"] == "ONBOARDING_TEMPLATE_NOT_FOUND"
    assert client.delete("/onboarding-templates/du-basic").status_code == 404


@pytest.mark.parametrize("name, body", [
    ("du", {**TEMPLATE, "changes": []}),                                                               # nothing to apply
    ("du", {**TEMPLATE, "requireBaseline": True}),                                                     # a baseline is required but none is named
    ("du", {**TEMPLATE, "changes": [{"managedElementRef": "ME-1", "attributeChanges": {"a": 1}}]}),    # the element is the new element's own
    ("du", {**TEMPLATE, "changes": [{"managedFunctionRef": "NRCellDU=", "attributeChanges": {"a": 1}}]}),
    ("du", {**TEMPLATE, "changes": [{"operation": "bogus"}]}),
    ("du", {**TEMPLATE, "unknown": 1}),
    ("-bad", TEMPLATE),
    ("has space", TEMPLATE),
])
def test_a_template_that_cannot_be_applied_is_refused(client, name, body):
    """A template with no changes, a required baseline with none named, a change that names its own element, a malformed ref or operation, an
    unknown field or a bad name is refused (422).
    """
    assert client.put(f"/onboarding-templates/{name}", json=body).status_code == 422


def test_a_template_name_that_is_not_one_is_a_404_on_read(client):
    """A name that is not shaped like a template name is 404 on read, the same as an unknown one."""
    assert client.get("/onboarding-templates/-bad").status_code == 404


# ---- MGT-14.2 discovery selects a template

def test_registering_an_element_selects_the_matching_template(client):
    """Registering an element of the template's type selects it, answers with the onboarding state and keeps the reported software version on the
    row.
    """
    _template(client)
    resp = _register(client, softwareVersion="1.0")
    assert resp["onboarding"] == {"status": "TEMPLATE_SELECTED", "templateName": "du-basic", "softwareCheck": "NOT_CHECKED"}
    row = _row(client)
    assert row["status"] == "TEMPLATE_SELECTED" and row["templateName"] == "du-basic" and row["softwareVersion"] == "1.0" and row["configJobId"] is None


def test_the_template_that_names_the_vendor_beats_the_general_one_and_another_vendors_is_not_considered(client):
    """A template naming the element's vendor beats a general one, and another vendor's template is not considered."""
    _template(client, "du-any")
    _template(client, "du-acme", vendorName="acme")
    _template(client, "du-other", vendorName="other")
    assert _register(client, "ME-1", vendorName="acme")["onboarding"]["templateName"] == "du-acme"
    assert _register(client, "ME-2", vendorName="third")["onboarding"]["templateName"] == "du-any"
    assert _register(client, "ME-3")["onboarding"]["templateName"] == "du-any"


def test_among_equal_templates_the_first_by_name_wins(client):
    """Among equally specific templates the first by name is chosen, so the choice is stable."""
    _template(client, "b-du")
    _template(client, "a-du")
    assert _register(client)["onboarding"]["templateName"] == "a-du"


def test_an_element_no_template_fits_is_flagged_not_dropped(client):
    """An element no enabled template fits is NO_TEMPLATE with a detail, and applying is 409 since there is nothing to apply."""
    _template(client, entityType="O-CU")
    resp = _register(client)
    assert resp["onboarding"]["status"] == "NO_TEMPLATE"
    assert "no enabled template" in _row(client)["detail"]
    assert _apply(client).status_code == 409                                 # there is nothing to apply


def test_select_is_the_way_in_for_an_element_registered_before_any_template_and_after_a_change(client):
    """Select matches an element that was registered before any template existed, can pick a named template, and returns it to NO_TEMPLATE when the
    templates are gone.
    """
    _register(client)
    assert client.get("/element-onboarding/ME-1").status_code == 404
    _template(client)
    selected = client.post("/element-onboarding/ME-1/select", json={})
    assert selected.status_code == 200 and selected.json()["status"] == "TEMPLATE_SELECTED" and selected.json()["templateName"] == "du-basic"
    _template(client, "du-better", vendorName=None, description="x")
    assert client.post("/element-onboarding/ME-1/select", json={"template": "du-better"}).json()["templateName"] == "du-better"
    client.delete("/onboarding-templates/du-basic")
    client.delete("/onboarding-templates/du-better")
    assert client.post("/element-onboarding/ME-1/select", json={}).json()["status"] == "NO_TEMPLATE"


def test_select_refuses_a_template_for_another_type_an_unknown_template_an_unknown_element_and_one_being_applied(client, db_session_factory):
    """Select refuses a template for another entity type (422), an unknown template or element (404) and an element whose template is being applied
    (409).
    """
    _template(client)
    _template(client, "cu", entityType="O-CU")
    _register(client)
    wrong = client.post("/element-onboarding/ME-1/select", json={"template": "cu"})
    assert wrong.status_code == 422 and "O-CU" in wrong.json()["detail"]["detail"]
    assert client.post("/element-onboarding/ME-1/select", json={"template": "nope"}).json()["detail"]["title"] == "ONBOARDING_TEMPLATE_NOT_FOUND"
    assert client.post("/element-onboarding/ME-404/select", json={}).status_code == 404
    with db_session_factory() as db:
        db.get(ElementOnboarding, "ME-1").status = "APPLYING"
        db.commit()
    busy = client.post("/element-onboarding/ME-1/select", json={})
    assert busy.status_code == 409 and busy.json()["detail"]["title"] == "LIFECYCLE_ILLEGAL_TRANSITION"


# ---- MGT-14.3 and 14.5 applying the template, the status FSM

def test_applying_the_template_writes_it_as_a_config_job_and_the_element_is_onboarded(client, db_session_factory, nf):
    """Applying writes the template as a config job requested by `onboarding:<template>`, and the element ends ONBOARDED with the job id on its
    row.
    """
    _template(client)
    _register(client)
    resp = _apply(client)
    assert resp.status_code == 202 and resp.json()["status"] == "ONBOARDED" and resp.json()["detail"] is None
    assert nf["edits"] == [("ME-1", "NRCellDU=1", {"txPower": 20}), ("ME-1", None, {"adminState": "unlocked"})]
    job_id = resp.json()["configJobId"]
    job = client.get(f"/config-jobs/{job_id}").json()
    assert job["status"] == "COMPLETED" and job["requestedBy"] == "onboarding:du-basic"
    assert _row(client)["status"] == "ONBOARDED"
    assert [r["managedElementRef"] for r in client.get("/element-onboarding", params={"status": "ONBOARDED"}).json()["items"]] == ["ME-1"]
    assert client.get("/element-onboarding", params={"status": "FAILED"}).json()["items"] == []
    assert _alarms(db_session_factory) == []


def test_a_refused_write_ends_failed_with_the_reason_and_an_alarm_and_applying_again_can_succeed(client, db_session_factory, nf):
    """A rejected write ends the onboarding FAILED with the job id and the reason, raises a major alarm, and applying again after the cause is
    fixed succeeds with a new job.
    """
    _template(client)
    _register(client)
    nf["fail"] = True
    failed = _apply(client).json()
    assert failed["status"] == "FAILED" and "ended FAILED" in failed["detail"] and "NETCONF_RPC_FAILED" in failed["detail"]
    assert failed["configJobId"] is not None
    assert [(s, c) for s, sev, c in _alarms(db_session_factory) if sev == "major" and s == "onboarding:ME-1"] == [("onboarding:ME-1", "ONBOARDING_FAILED")]
    nf["fail"] = False
    again = _apply(client).json()
    assert again["status"] == "ONBOARDED" and again["detail"] is None and again["configJobId"] != failed["configJobId"]


def test_a_write_the_service_refuses_up_front_is_failed_too(client, nf):
    """A write refused before a job exists (the element has no PROV service) also ends FAILED, with the reason and no job id, and nothing is sent."""
    _template(client)
    _register(client, supportedServices=["FM"])                              # no Provisioning service: the config job is refused before it exists
    row = _apply(client).json()
    assert row["status"] == "FAILED" and "O1_SERVICE_NOT_SUPPORTED" in row["detail"] and row["configJobId"] is None
    assert nf["edits"] == []


def test_an_unexpected_error_leaves_the_row_failed_and_is_raised(client, db_session_factory, monkeypatch):
    """An unexpected error while writing leaves the row FAILED with only the exception's type (not its text) and is raised, not hidden."""
    _template(client)
    _register(client)

    def boom(*a, **kw):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr("app.main._execute_write", boom)
    with pytest.raises(RuntimeError):
        _apply(client)
    row = _row(client)
    assert row["status"] == "FAILED" and "RuntimeError" in row["detail"] and "disk on fire" not in row["detail"]


def test_apply_is_refused_when_no_template_is_selected_or_it_is_already_being_applied_or_the_template_is_gone(client, db_session_factory, nf):
    """Apply is 404 when there is no onboarding row, 409 while another apply is running, and 404 ONBOARDING_TEMPLATE_NOT_FOUND when the selected
    template has been deleted, sending nothing.
    """
    assert _apply(client, "ME-9").status_code == 404                         # no onboarding row at all
    _template(client)
    _register(client)
    with db_session_factory() as db:
        db.get(ElementOnboarding, "ME-1").status = "APPLYING"
        db.commit()
    assert _apply(client).status_code == 409                                  # a second apply while one runs
    with db_session_factory() as db:
        db.get(ElementOnboarding, "ME-1").status = "TEMPLATE_SELECTED"
        db.commit()
    client.delete("/onboarding-templates/du-basic")
    gone = _apply(client)
    assert gone.status_code == 404 and gone.json()["detail"]["title"] == "ONBOARDING_TEMPLATE_NOT_FOUND"
    assert nf["edits"] == []


def test_an_onboarded_element_can_be_applied_again(client, nf):
    """An ONBOARDED element can be applied again."""
    _template(client)
    _register(client)
    assert _apply(client).json()["status"] == "ONBOARDED"
    assert _apply(client).json()["status"] == "ONBOARDED"
    assert len(nf["edits"]) == 4


def test_a_template_with_a_delete_or_create_change_carries_the_operation(client, nf, monkeypatch):
    """The operation of a template change (here `create`) reaches the adaptor."""
    seen = []

    def edit(uri, ref, attribute_changes, message_id, operation="merge", managed_function_ref=None):
        seen.append(operation)
        return EditResult(True)

    monkeypatch.setattr("app.main.send_edit_config", edit)
    _template(client, changes=[{"managedFunctionRef": "NRCellDU=9", "operation": "create", "attributeChanges": {"cellLocalId": 9}}])
    _register(client)
    assert _apply(client).json()["status"] == "ONBOARDED" and seen == ["create"]


# ---- MGT-14.4 software baseline

def test_the_baseline_check_matches_flags_a_mismatch_and_is_unchecked_without_a_version(client, db_session_factory):
    """The software baseline check is MATCH, MISMATCH (with one warning alarm that repeated findings do not duplicate) or NOT_CHECKED when no
    version was reported.
    """
    _template(client, softwareBaseline="2.1")
    assert _register(client, "ME-1", softwareVersion="2.1")["onboarding"]["softwareCheck"] == "MATCH"
    assert _alarms(db_session_factory, "ME-1") == []
    assert _register(client, "ME-2")["onboarding"]["softwareCheck"] == "NOT_CHECKED"
    mismatch = _register(client, "ME-3", softwareVersion="2.0")
    assert mismatch["onboarding"]["softwareCheck"] == "MISMATCH"
    assert _alarms(db_session_factory, "ME-3") == [("onboarding-software:ME-3", "warning", "SOFTWARE_BASELINE_MISMATCH")]
    assert [r["managedElementRef"] for r in client.get("/element-onboarding", params={"software_check": "MISMATCH"}).json()["items"]] == ["ME-3"]
    client.post("/element-onboarding/ME-3/select", json={})                    # the same finding again does not raise a second alarm
    assert len(_alarms(db_session_factory, "ME-3")) == 1


def test_a_mismatch_only_flags_unless_the_template_requires_the_baseline(client, nf):
    """A baseline mismatch only flags the row when the template does not require the baseline; the apply goes ahead."""
    _template(client, softwareBaseline="2.1")
    _register(client, softwareVersion="2.0")
    row = _apply(client).json()
    assert row["status"] == "ONBOARDED" and row["softwareCheck"] == "MISMATCH"


def test_a_required_baseline_stops_the_apply_until_the_element_runs_it(client, nf):
    """With `requireBaseline` a mismatch stops the apply (FAILED, no job, nothing sent) until the element reports the baseline version."""
    _template(client, softwareBaseline="2.1", requireBaseline=True)
    _register(client, softwareVersion="2.0")
    blocked = _apply(client).json()
    assert blocked["status"] == "FAILED" and "SOFTWARE_BASELINE_MISMATCH" in blocked["detail"] and "2.0" in blocked["detail"] and blocked["configJobId"] is None
    assert nf["edits"] == []
    ok = _apply(client, softwareVersion="2.1").json()                          # the element was upgraded: say so
    assert ok["status"] == "ONBOARDED" and ok["softwareCheck"] == "MATCH" and ok["softwareVersion"] == "2.1" and nf["edits"]


def test_a_required_baseline_with_no_reported_version_stops_the_apply(client, nf):
    """With `requireBaseline` an unreported version also stops the apply."""
    _template(client, softwareBaseline="2.1", requireBaseline=True)
    _register(client)
    blocked = _apply(client).json()
    assert blocked["status"] == "FAILED" and "not reported" in blocked["detail"]


# ---- MGT-14.3 auto apply at the first heartbeat

def _heartbeat(client, endpoint_id):
    resp = client.post(f"/o1-adaptor-endpoints/{endpoint_id}/heartbeat")
    assert resp.status_code == 200
    return resp.json()


def test_an_auto_apply_template_is_written_when_the_element_first_reports_in(client, nf):
    """An `autoApply` template is written at the element's first heartbeat, and later heartbeats write nothing."""
    _template(client, autoApply=True)
    endpoint = _register(client)["endpointId"]
    assert _row(client)["status"] == "TEMPLATE_SELECTED" and nf["edits"] == []     # registered, not yet heard from
    assert _heartbeat(client, endpoint)["healthStatus"] == "ACTIVE"
    row = _row(client)
    assert row["status"] == "ONBOARDED" and row["configJobId"] and len(nf["edits"]) == 2
    _heartbeat(client, endpoint)                                                 # later heartbeats write nothing
    assert len(nf["edits"]) == 2


def test_a_template_without_auto_apply_waits_for_an_operator(client, nf):
    """Without `autoApply` the first heartbeat leaves the element waiting for an operator."""
    _template(client)
    endpoint = _register(client)["endpointId"]
    _heartbeat(client, endpoint)
    assert _row(client)["status"] == "TEMPLATE_SELECTED" and nf["edits"] == []


def test_an_element_without_an_onboarding_row_or_with_a_template_that_changed_is_left_alone_by_the_heartbeat(client, nf):
    """A heartbeat does nothing for an element with no onboarding row, or whose template stopped being auto-apply or was deleted before it came up."""
    plain = _register(client, "ME-1")["endpointId"]                                  # no template yet: no row
    _heartbeat(client, plain)
    _template(client, autoApply=True)
    later = _register(client, "ME-2")["endpointId"]
    client.put("/onboarding-templates/du-basic", json={**TEMPLATE, "autoApply": False})   # the operator turned it off before the element came up
    _heartbeat(client, later)
    assert _row(client, "ME-2")["status"] == "TEMPLATE_SELECTED" and nf["edits"] == []
    client.post("/element-onboarding/ME-2/select", json={})
    client.delete("/onboarding-templates/du-basic")                                  # and then deleted it
    third = _register(client, "ME-3")                                                  # no template left: no row, as before
    assert "onboarding" not in third
    _heartbeat(client, third["endpointId"])
    assert nf["edits"] == [] and client.get("/element-onboarding/ME-3").status_code == 404


def test_a_failing_auto_apply_does_not_fail_the_heartbeat(client, nf):
    """If the automatic apply fails the heartbeat is still answered ACTIVE, and the failure is on the onboarding row."""
    _template(client, autoApply=True)
    endpoint = _register(client)["endpointId"]
    nf["fail"] = True
    assert _heartbeat(client, endpoint)["healthStatus"] == "ACTIVE"
    assert _row(client)["status"] == "FAILED"


def test_an_error_in_the_auto_apply_is_logged_and_the_heartbeat_still_answers(client, monkeypatch):
    """An unexpected error in the automatic apply is swallowed (and logged) so the heartbeat still answers."""
    _template(client, autoApply=True)
    endpoint = _register(client)["endpointId"]

    def boom(*a, **kw):
        raise RuntimeError("x")

    monkeypatch.setattr(lifecycle, "apply_template", boom)
    assert _heartbeat(client, endpoint)["healthStatus"] == "ACTIVE"


# ---- the FSM

def test_the_onboarding_fsm_allows_selecting_again_and_applying_again_but_not_applying_twice_at_once():
    """The onboarding state machine allows selecting again and applying again from ONBOARDED or FAILED, and refuses an apply while APPLYING and the
    other illegal events.
    """
    fire = ONBOARDING_FSM.fire
    S, E = OnboardingState, OnboardingEvent
    assert fire(S.DISCOVERED, E.TEMPLATE_MATCHED) == S.TEMPLATE_SELECTED and fire(S.DISCOVERED, E.NO_MATCH) == S.NO_TEMPLATE
    assert fire(S.NO_TEMPLATE, E.TEMPLATE_MATCHED) == S.TEMPLATE_SELECTED
    assert fire(S.TEMPLATE_SELECTED, E.APPLY) == S.APPLYING and fire(S.APPLYING, E.APPLIED) == S.ONBOARDED and fire(S.APPLYING, E.APPLY_FAILED) == S.FAILED
    assert fire(S.FAILED, E.APPLY) == S.APPLYING and fire(S.ONBOARDED, E.APPLY) == S.APPLYING
    assert fire(S.FAILED, E.TEMPLATE_MATCHED) == S.TEMPLATE_SELECTED
    for state, event in ((S.APPLYING, E.APPLY), (S.APPLYING, E.TEMPLATE_MATCHED), (S.APPLYING, E.NO_MATCH), (S.NO_TEMPLATE, E.APPLY), (S.DISCOVERED, E.APPLY),
                         (S.ONBOARDED, E.APPLIED), (S.TEMPLATE_SELECTED, E.APPLIED)):
        with pytest.raises(IllegalTransition):
            fire(state, event)


# ---- scope (PR-SEC-10)

def _scoped(region):
    return {"X-R1-Invoker-Id": "x", "X-R1-Role": "rapp", SCOPE_HEADER: json.dumps({"regions": [region]}, sort_keys=True, separators=(",", ":"))}


def test_a_scoped_caller_sees_and_applies_only_its_own_elements(client, db_session_factory, nf):
    """A caller with a region claim sees only the onboarding of its own elements; reading, selecting or applying another's is 403 SCOPE_DENIED,
    while an unscoped caller sees all.
    """
    _template(client)
    _register(client, "ME-1", region="eu")
    _register(client, "ME-2", region="us")
    eu = _scoped("eu")
    assert [r["managedElementRef"] for r in client.get("/element-onboarding", headers=eu).json()["items"]] == ["ME-1"]
    assert client.get("/element-onboarding/ME-1", headers=eu).status_code == 200
    assert client.get("/element-onboarding/ME-2", headers=eu).json()["detail"]["title"] == "SCOPE_DENIED"
    assert client.post("/element-onboarding/ME-2/apply", headers=eu, json={"requestedBy": "x"}).status_code == 403
    assert client.post("/element-onboarding/ME-2/select", headers=eu, json={}).status_code == 403
    assert client.post("/element-onboarding/ME-1/apply", headers=eu, json={"requestedBy": "x"}).json()["status"] == "ONBOARDED"
    assert len(client.get("/element-onboarding").json()["items"]) == 2


def test_the_single_rows_view_has_the_fields_it_documents(client):
    """One onboarding row has exactly the fields the API documents."""
    _template(client, softwareBaseline="1")
    _register(client, softwareVersion="1")
    row = _row(client)
    assert set(row) == {"managedElementRef", "status", "templateName", "softwareVersion", "softwareBaseline", "softwareCheck", "configJobId", "detail", "createdAt", "updatedAt"}
    assert row["softwareBaseline"] == "1"


def test_apply_uses_the_main_module_it_was_bound_to_not_a_lookup_by_name(monkeypatch):
    """The contract tests load several services in one process, so `app.main` can name another service's module at call time; main hands its own over once."""
    import sys
    import types

    from app import lifecycle, main as ran_main

    monkeypatch.setitem(sys.modules, "app.main", types.ModuleType("app.main"))      # another service's module under the same name
    assert lifecycle._main_module() is ran_main and hasattr(lifecycle._main_module(), "WriteConfigRequest")
