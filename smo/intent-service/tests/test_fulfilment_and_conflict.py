"""GUI-9.8 in the Intent Service: the intent view's `fulfilmentPercent`, `fulfilled` and `inConflict`, kept from the newest reports, and the
`fulfilled` / `in_conflict` filters of `GET /intents`; plus the percent rule itself (`fulfilment_percent`), which revision 0037 repeats.

Fixtures and builders come from `test_main.py` (`client` on SQLite, `_register_rmih`, `_intent`, `_expectation`); reports are published through
`POST /intent-reports`. Run: `cd smo/intent-service && PYTHONPATH=.:../shared python -m pytest tests/test_fulfilment_and_conflict.py -q`.
"""

import pytest

from test_main import _expectation, _intent, _register_rmih, client  # noqa: F401  (pytest fixture)

from app.main import fulfilment_percent

FULFILLED = {"fulfilmentStatus": "FULFILLED"}
NOT_FULFILLED = {"fulfilmentStatus": "NOT_FULFILLED", "notFullfilledState": "DEGRADED"}


def _create(client, **extra):
    """Creates an intent with one RAN expectation on SubNetwork=1 (registering the handling function first if needed) and returns its id."""
    if client.get("/intent-handling-functions").json()["total"] == 0:
        _register_rmih(client)
    body = _intent(expectations=[_expectation(instance="SubNetwork=1", **extra)])
    return client.post("/intents", json=body).json()["intentId"]


def _targets_report(*statuses, overall="NOT_FULFILLED"):
    """A fulfilment report with one expectation whose targets have the given statuses, and the intent's overall status `overall`."""
    return {"intentFulfilmentInfo": {"fulfilmentStatus": overall}, "expectationFulfilmentResult": [{
        "expectaitonId": "e1", "expectationFulfilmentInfo": {"fulfilmentStatus": overall},
        "targetFulfilmentResults": [{"targetName": f"t{i}", "targetFulfilmentInfo": {"fulfilmentStatus": s}} for i, s in enumerate(statuses)]}]}


def _publish(client, intent_id, **kinds):
    """Publishes a report of the given kinds for the intent and asserts it was accepted."""
    resp = client.post("/intent-reports", json={"intentReference": intent_id, **kinds})
    assert resp.status_code == 201, resp.text


def test_a_new_intent_is_zero_percent_fulfilled_and_not_in_conflict(client):
    """The initial RECEIVED report counts: 0 % fulfilled, not fulfilled, and no conflict."""
    view = client.get(f"/intents/{_create(client)}").json()
    assert (view["fulfilmentPercent"], view["fulfilled"], view["inConflict"]) == (0.0, False, False)


def test_the_percent_follows_the_newest_fulfilment_report(client):
    """Three of four targets fulfilled is 75 %; a later fulfilment report replaces it."""
    intent_id = _create(client)
    _publish(client, intent_id, intentFulfilmentReport=_targets_report("FULFILLED", "FULFILLED", "FULFILLED", "NOT_FULFILLED"))
    assert client.get(f"/intents/{intent_id}").json()["fulfilmentPercent"] == 75.0
    _publish(client, intent_id, intentFulfilmentReport=_targets_report("FULFILLED", "FULFILLED", overall="FULFILLED"))
    view = client.get(f"/intents/{intent_id}").json()
    assert view["fulfilmentPercent"] == 100.0 and view["fulfilled"] is True


def test_a_handlers_conflict_report_sets_in_conflict_and_an_empty_one_clears_it(client):
    """A published conflict report puts the intent in conflict; a later report with an empty conflict list says it is resolved."""
    intent_id = _create(client)
    conflict = {"conflictId": "c1", "conflictType": "INTENT_CONFLICT", "recommendedSolutions": "MODIFY"}
    _publish(client, intent_id, intentConflictReports=[conflict])
    assert client.get(f"/intents/{intent_id}").json()["inConflict"] is True
    _publish(client, intent_id, intentFulfilmentReport=_targets_report("FULFILLED"))
    assert client.get(f"/intents/{intent_id}").json()["inConflict"] is True          # a fulfilment report says nothing about conflicts
    _publish(client, intent_id, intentConflictReports=[])
    assert client.get(f"/intents/{intent_id}").json()["inConflict"] is False


def test_a_conflict_found_at_creation_shows_on_the_new_intent(client):
    """The target conflict the service itself finds when an intent is created makes that intent `inConflict`."""
    _create(client)
    second = _create(client, targets=[{"targetName": "RANEnergyConsumption", "targetCondition": "IS_LESS_THAN", "targetValueRange": 300}])
    assert client.get(f"/intents/{second}").json()["inConflict"] is True


def test_deactivation_resets_the_percent_to_its_suspended_report(client):
    """Deactivating writes a NOT_FULFILLED / SUSPENDED report, so the intent no longer shows as fulfilled."""
    intent_id = _create(client)
    _publish(client, intent_id, intentFulfilmentReport=_targets_report("FULFILLED", overall="FULFILLED"))
    view = client.patch(f"/intents/{intent_id}/admin-state", json={"newState": "DEACTIVATED", "requesterId": "rapp-1"}).json()
    assert (view["fulfilmentPercent"], view["fulfilled"]) == (0.0, False)


def test_the_list_filters_on_fulfilled_and_in_conflict(client):
    """`fulfilled` and `in_conflict` select in SQL on the newest reports' verdicts, each in both directions."""
    done, conflicted, plain = _create(client), _create(client), _create(client)
    _publish(client, done, intentFulfilmentReport=_targets_report("FULFILLED", overall="FULFILLED"))
    _publish(client, conflicted, intentConflictReports=[{"conflictId": "c", "conflictType": "INTENT_CONFLICT"}])

    def ids(**params):
        # the ids of the intents the list answers for these filters
        return {i["intentId"] for i in client.get("/intents", params=params).json()["items"]}

    assert ids(fulfilled=True) == {done} and ids(fulfilled=False) == {conflicted, plain}
    assert ids(in_conflict=True) == {conflicted} and ids(in_conflict=False) == {done, plain}
    assert ids(fulfilled=False, in_conflict=False) == {plain}


# The percent rule on report shapes: targets first, then expectations, then the intent's own verdict; nothing to count is None.
@pytest.mark.parametrize("report, percent", [
    (None, None),
    ({"intentFulfilmentInfo": FULFILLED}, 100.0),
    ({"intentFulfilmentInfo": NOT_FULFILLED}, 0.0),
    (_targets_report("FULFILLED", "NOT_FULFILLED", "NOT_FULFILLED"), 33.3),
    ({"intentFulfilmentInfo": NOT_FULFILLED, "expectationFulfilmentResult": [
        {"expectaitonId": "a", "expectationFulfilmentInfo": FULFILLED}, {"expectaitonId": "b", "expectationFulfilmentInfo": NOT_FULFILLED}]}, 50.0),
    ({}, None),
])
def test_the_percent_rule(report, percent):
    """The share is counted over the most detailed level the report gives, rounded to one decimal."""
    assert fulfilment_percent(report) == percent
