"""PR-GUI-3 (GUI-3.1, GUI-3.3): `GET /topology/graph`, the containment tree as nodes and edges with each node's open alarms."""

import datetime

from test_mo_tree import _register, client, db_session_factory  # noqa: F401  (pytest fixtures and the registration helper)

from app import mo_tree
from app.models import Alarm, ManagedEntity


def _alarm(db_session_factory, element, severity, function=None):
    """Stores an alarm on `element` (and `function` below it) with `severity`, as the FM path would."""
    db = db_session_factory()
    db.add(Alarm(source_alarm_id=f"{element}-{function}-{severity}-{datetime.datetime.now().timestamp()}", managed_element_ref=element,
                 managed_function_ref=function, severity=severity, raised_at=datetime.datetime.now(datetime.UTC)))
    db.commit()
    db.close()


def _tree(client, db_session_factory):
    """Two elements: ME-1 with a cell below a DU function, ME-2 with only its root; ME-1 is in region east."""
    _register(client, "ME-1", "GNBDUFunction=1,NRCellDU=101")
    _register(client, "ME-2")
    db = db_session_factory()
    db.get(ManagedEntity, "ME-1").region = "east"
    db.commit()
    db.close()


def test_the_graph_is_the_trees_nodes_and_parent_links(client, db_session_factory):
    """Every node of the tree once, in DN order, with an edge from each node to its parent; nothing is cut."""
    _tree(client, db_session_factory)
    body = client.get("/topology/graph").json()
    dns = [n["dn"] for n in body["nodes"]]
    cell = "ManagedElement=ME-1,GNBDUFunction=1,NRCellDU=101"
    assert dns == sorted(dns) and cell in dns and "ManagedElement=ME-2" in dns and body["total"] == 4 and body["truncated"] is False
    assert {"child": cell, "parent": "ManagedElement=ME-1,GNBDUFunction=1"} in body["edges"] and len(body["edges"]) == 2
    assert next(n for n in body["nodes"] if n["dn"] == cell)["class"] == "NRCellDU"


def test_open_alarms_are_placed_on_their_node_and_the_worst_is_named(client, db_session_factory):
    """An alarm on a function lands on that node, one without a function (or naming a node the tree lacks) on the element's root; cleared ones are not counted."""
    _tree(client, db_session_factory)
    _alarm(db_session_factory, "ME-1", "minor", "GNBDUFunction=1,NRCellDU=101")
    _alarm(db_session_factory, "ME-1", "critical", "GNBDUFunction=1,NRCellDU=101")
    _alarm(db_session_factory, "ME-1", "cleared", "GNBDUFunction=1,NRCellDU=101")
    _alarm(db_session_factory, "ME-1", "major")
    _alarm(db_session_factory, "ME-2", "warning", "GNBCUCPFunction=9")                    # not in the tree: shown on ME-2's root
    nodes = {n["dn"]: n for n in client.get("/topology/graph").json()["nodes"]}
    cell = nodes["ManagedElement=ME-1,GNBDUFunction=1,NRCellDU=101"]
    assert cell["alarms"] == {"minor": 1, "critical": 1} and cell["worst"] == "critical"
    assert nodes["ManagedElement=ME-1"]["alarms"] == {"major": 1} and nodes["ManagedElement=ME-1,GNBDUFunction=1"]["worst"] is None
    assert nodes["ManagedElement=ME-2"]["worst"] == "warning"


def test_the_graph_is_narrowed_by_element_and_place_and_capped(client, db_session_factory):
    """`managed_element_ref` and `region` keep one element's nodes; `max_nodes` cuts the answer, says so, and keeps only the edges between nodes it returns."""
    _tree(client, db_session_factory)
    assert {n["managedElementRef"] for n in client.get("/topology/graph", params={"managed_element_ref": "ME-2"}).json()["nodes"]} == {"ME-2"}
    assert {n["managedElementRef"] for n in client.get("/topology/graph", params={"region": "east"}).json()["nodes"]} == {"ME-1"}
    cut = client.get("/topology/graph", params={"max_nodes": 2}).json()
    assert len(cut["nodes"]) == 2 and cut["total"] == 4 and cut["truncated"] is True
    assert all(e["child"] in {n["dn"] for n in cut["nodes"]} and e["parent"] in {n["dn"] for n in cut["nodes"]} for e in cut["edges"])
    assert client.get("/topology/graph", params={"max_nodes": 0}).status_code == 422
    assert client.get("/topology/graph", params={"max_nodes": 2001}).status_code == 422


# The root DN helper is what places an element-level alarm; a DN-keyed element is its own root, so its alarms land on it too.
def test_an_alarm_of_a_dn_keyed_element_lands_on_its_root(client, db_session_factory):
    """A DN-keyed element's alarm without a function is counted on the element's own DN."""
    _register(client, "SubNetwork=A,ManagedElement=ME-9")
    _alarm(db_session_factory, "SubNetwork=A,ManagedElement=ME-9", "major")
    nodes = {n["dn"]: n for n in client.get("/topology/graph").json()["nodes"]}
    assert nodes[mo_tree.root_dn("SubNetwork=A,ManagedElement=ME-9")]["worst"] == "major"
