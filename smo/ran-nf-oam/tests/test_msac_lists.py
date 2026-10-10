"""MGT-2.6 (docs/adr/0005-tenant-region-authorization.md section 9, `HISTORY.md` PR-MGT-2): with `RAN_NF_OAM_MSAC_REACH` on, the list routes leave out what a managed caller's
access rules do not let it `read` (filtered, never refused, as the scope is), and a managed caller that may not read a subscription's element cannot remove the subscription (204,
nothing removed). Off, or for a caller that is not a registered MSAC Identity, nothing changes. Run with: pytest tests/test_msac_lists.py -q
"""

import datetime
import json
import uuid

import pytest

from app import mo_tree
from app.models import Alarm, FileSubscription, FMSubscription, ManagedEntity, PMFile, PMSubscription, SoftwareManagementJob, WriteConfigJob, WriteConfigSubChange

from test_main import client, db_session_factory  # noqa: F401  (pytest fixtures)
from test_msac_reach import as_, denied, identity, rule
from test_waves import ELEMENTS, fleet  # noqa: F401


@pytest.fixture
def on(monkeypatch):
    monkeypatch.setenv("RAN_NF_OAM_MSAC_REACH", "on")


@pytest.fixture
def world(client, fleet):
    """ME-1..ME-4 with one of everything each. `reader` may read ME-1 and ME-2, `nobody` is an Identity with no rule, `everything` may read `/*`."""
    t0 = datetime.datetime(2026, 10, 1, 12, 0, tzinfo=datetime.UTC)
    with fleet["db"]() as db:
        for ref in ELEMENTS:
            db.add(Alarm(source_alarm_id=f"a-{ref}", managed_element_ref=ref, severity="major"))
            db.add(PMSubscription(managed_element_ref=ref, counter_type="c", delivery_method="pull", southbound_engine="ProvMnS"))
            db.add(FMSubscription(managed_element_ref=ref, delivery_method="pull", southbound_engine="FaultMnS"))
            db.add(SoftwareManagementJob(managed_element_ref=ref))
            content = json.dumps({"measurements": [{"cellId": "1", "timestamp": t0.isoformat(), "value": 10.0 if ref in ("ME-1", "ME-2") else 90.0}]})
            db.add(PMFile(managed_element_ref=ref, counter_type="RRU.PrbTotDl", content=content, file_size=len(content)))
            mo_tree.sync_registry(db, db.get(ManagedEntity, ref))
        db.commit()
    for ref in ELEMENTS:
        assert client.put(f"/managed-entities/{ref}/cells/{ref[-1]}/guards", json={"cellClass": "NORMAL", "neighbourRefs": ["1" if ref != "ME-1" else "2"]}).status_code == 200
    assert client.put("/kpi-definitions/prb", json={"formula": "prb", "unit": "%", "counters": [{"counter": "RRU.PrbTotDl", "variable": "prb", "aggregation": "avg"}]}).status_code == 200
    identity(client, "reader", rule(client, "/ManagedElement=ME-1", ["read"]), rule(client, "/ManagedElement=ME-2", ["read"]))
    identity(client, "nobody")
    identity(client, "everything", rule(client, "/*", ["read"]))
    return fleet


def _refs(items, key="managedElementRef"):
    return {i[key] for i in items}


def _kpi(client, headers):
    params = {"from_time": "2026-10-01T00:00:00Z", "to_time": "2026-10-02T00:00:00Z", "group_by": "element"}
    return _refs([i["group"] for i in client.get("/kpis/prb", headers=headers, params=params).json()["items"]])


LISTS = {
    "alarms": lambda c, h: _refs(c.get("/alarms", headers=h).json()["items"]),
    "pm subscriptions": lambda c, h: _refs(c.get("/pm-subscriptions", headers=h).json()["items"]),
    "fm subscriptions": lambda c, h: _refs(c.get("/fm-subscriptions", headers=h).json()["items"]),
    "software jobs": lambda c, h: _refs(c.get("/software-management-jobs", headers=h).json()["items"]),
    "endpoints": lambda c, h: _refs(c.get("/o1-adaptor-endpoints", headers=h).json()["items"]),
    "managed entities": lambda c, h: _refs(c.get("/managed-entities", headers=h).json()["items"]),
    "cell guards": lambda c, h: _refs(c.get("/cell-guards", headers=h).json()["items"]),
    "kpi": _kpi,
    "topology": lambda c, h: {a["attributes"]["managedElementRef"] for g in c.get("/topology", headers=h).json()["entities"] for a in g["o-ran-smo-teiv-ran:ManagedObject"]},
    "links": lambda c, h: {l["aElement"] for l in c.get("/topology/links", headers=h).json()["items"]},
}
EVERY = set(ELEMENTS)


# Table: one row per list route in `LISTS` (alarms, subscriptions, jobs, endpoints, elements, guards, KPIs, topology, links), each reduced to the set of elements it shows.
@pytest.mark.parametrize("name", LISTS)
def test_a_list_leaves_out_what_the_callers_rules_do_not_let_it_read(client, world, on, name):
    """With the switch on, a registered Identity sees only the elements its rules let it read (none for one without rules, all for `/*`), while an unregistered caller or one outside the gateway sees everything."""
    seen = LISTS[name]
    assert seen(client, as_("reader")) == {"ME-1", "ME-2"}
    assert seen(client, as_("nobody")) == set()
    assert seen(client, as_("everything")) == EVERY
    assert seen(client, as_("stranger")) == EVERY                  # not a registered Identity: not asked, as for writes
    assert seen(client, {}) == EVERY                                # did not come through the gateway


# Table: the same rows of `LISTS`, run with the switch off.
@pytest.mark.parametrize("name", LISTS)
def test_nothing_changes_until_the_switch_is_on(client, world, monkeypatch, name):
    """With `RAN_NF_OAM_MSAC_REACH` off every list shows every element to every caller, so shipping the code changes nothing until the switch is turned on."""
    monkeypatch.delenv("RAN_NF_OAM_MSAC_REACH", raising=False)
    for caller in ("reader", "nobody", "stranger"):
        assert LISTS[name](client, as_(caller)) == EVERY, caller


def test_the_total_of_a_filtered_page_does_not_count_what_is_hidden(client, world, on):
    """The `total` of a filtered page counts only what the caller may read, so the total does not reveal how many rows are hidden."""
    page = client.get("/alarms", headers=as_("reader")).json()
    assert len(page["items"]) == 2 and page["total"] == 2
    assert client.get("/alarms").json()["total"] == 4


def test_a_job_is_listed_only_when_every_element_it_wrote_to_is_readable(client, world, on):
    """A configuration job is listed only when every element it wrote to is readable by the caller, so a job spanning a hidden element stays hidden."""
    with world["db"]() as db:
        for name, elements in (("one", ["ME-1"]), ("both", ["ME-1", "ME-2"]), ("mixed", ["ME-1", "ME-3"]), ("far", ["ME-3"])):
            job = WriteConfigJob(requested_by=name, scope="cell")
            db.add(job)
            db.flush()
            for position, ref in enumerate(elements):
                db.add(WriteConfigSubChange(job_id=job.job_id, managed_element_ref=ref, attribute_changes={}, status="APPLIED", position=position, wave=1))
        db.commit()
    names = lambda headers: {j["requestedBy"] for j in client.get("/config-jobs", headers=headers).json()["items"]}      # noqa: E731
    assert names(as_("reader")) == {"one", "both"} and names(as_("nobody")) == set() and names(as_("stranger")) == names({}) == {"one", "both", "mixed", "far"}


def test_a_node_of_the_tree_needs_read_on_its_element(client, world, on):
    """Reading a managed object, its children or subtree, or a relation between two nodes needs `read` on the element of each node, and the refusal names the missing permission."""
    dn = "ManagedElement=ME-3"
    assert denied(client.get(f"/managed-objects/{dn}", headers=as_("reader"))).startswith("reader is not permitted: read /ManagedElement=ME-3")
    assert client.get("/managed-objects/ManagedElement=ME-1", headers=as_("reader")).status_code == 200
    assert client.get("/managed-objects/ManagedElement=ME-1/children", headers=as_("reader")).status_code in (200, 404)
    denied(client.get(f"/managed-objects/{dn}/subtree", headers=as_("nobody")))
    assert client.get(f"/managed-objects/{dn}", headers=as_("stranger")).status_code == 200
    assert client.get("/topology/relation", headers=as_("everything"), params={"a": "ManagedElement=ME-1", "b": dn}).json()["relation"] == "DIFFERENT_ELEMENT"
    denied(client.get("/topology/relation", headers=as_("reader"), params={"a": "ManagedElement=ME-1", "b": dn}))


def test_a_neighbour_on_an_element_the_caller_may_not_read_is_external_to_it(client, world, on):
    """In the links list a neighbour cell on an unreadable element is not shown, while links between readable elements stay INTER_ELEMENT."""
    links = {(l["aCell"], l["bCell"]): l for l in client.get("/topology/links", headers=as_("reader")).json()["items"]}
    assert links[("1", "2")]["linkType"] == "INTER_ELEMENT" and links[("2", "1")]["linkType"] == "INTER_ELEMENT"
    assert "3" not in {a for a, _ in links}


def test_subscriptions_are_removed_by_those_who_may_read_their_element_only(client, world, on):
    """Deleting a PM or FM subscription answers 204 for everyone, but removes it only when the caller may read its element (or is not a registered Identity), so a refusal does not reveal that it exists."""
    with world["db"]() as db:
        ids = {(kind, ref): row.subscription_id for kind, model in (("pm", PMSubscription), ("fm", FMSubscription)) for row in db.query(model) for ref in [row.managed_element_ref]}

    def count(model):
        with world["db"]() as db:
            return db.query(model).count()

    for kind, model, path in (("pm", PMSubscription, "pm-subscriptions"), ("fm", FMSubscription, "fm-subscriptions")):
        assert client.delete(f"/{path}/{ids[(kind, 'ME-1')]}", headers=as_("nobody")).status_code == 204          # says nothing ...
        assert client.delete(f"/{path}/{ids[(kind, 'ME-3')]}", headers=as_("reader")).status_code == 204          # ... also for an element it may read nowhere near
        assert count(model) == 4                                                                                    # ... and removes nothing
        assert client.delete(f"/{path}/{ids[(kind, 'ME-1')]}", headers=as_("reader")).status_code == 204
        assert count(model) == 3
        assert client.delete(f"/{path}/{ids[(kind, 'ME-3')]}", headers=as_("stranger")).status_code == 204         # not an Identity: not asked
        assert count(model) == 2
        assert client.delete(f"/{path}/{uuid.uuid4()}", headers=as_("nobody")).status_code == 204


def test_a_file_subscription_is_removed_by_whoever_may_read_the_whole_network(client, world, on):
    """A file subscription belongs to the whole network, so only a caller that may read everything removes it; others get 204 and nothing is removed."""
    body = {"consumerReference": "http://consumer.example/notify", "fileDataType": "Performance"}
    sub = client.post("/file-subscriptions", json=body, headers=as_("everything")).json()["subscriptionId"]
    for caller in ("reader", "nobody"):
        assert client.delete(f"/file-subscriptions/{sub}", headers=as_(caller)).status_code == 204
        with world["db"]() as db:
            assert db.query(FileSubscription).count() == 1
    assert client.delete(f"/file-subscriptions/{sub}", headers=as_("everything")).status_code == 204
    with world["db"]() as db:
        assert db.query(FileSubscription).count() == 0


def test_the_deletes_are_unchanged_with_the_switch_off(client, world, monkeypatch):
    """With the switch off a delete by an Identity without any rule still removes the subscription, as before the rule existed."""
    monkeypatch.delenv("RAN_NF_OAM_MSAC_REACH", raising=False)
    with world["db"]() as db:
        sub = db.query(PMSubscription).first().subscription_id
    assert client.delete(f"/pm-subscriptions/{sub}", headers=as_("nobody")).status_code == 204
    with world["db"]() as db:
        assert db.query(PMSubscription).count() == 3
