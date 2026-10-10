"""MGT-10.2: link types in the topology: the neighbour relations of the cell guards, and containment relations between managed objects. PR-GUI-9.4: the
links' paging, the `reciprocal` filter and `GET /topology/links/counts`."""

import pytest

from test_main import client, db_session_factory  # noqa: F401  (pytest fixtures)

from app.models import ManagedEntity, ManagedObject


def _element(db, ref, cells):
    db.add(ManagedEntity(managed_element_ref=ref, entity_type="O-DU", o1_protocol="NETCONF", cell_guards=cells))


def _cell(neighbours=(), group=None, zone=None):
    return {"cellClass": "NORMAL", "sectorGroup": group, "incidentZone": zone, "neighbourRefs": list(neighbours)}


@pytest.fixture
def network(db_session_factory):
    """Fixture: four elements whose cell guards declare neighbours of every kind: same element, another element, an unmanaged cell, and a cell id claimed by two elements.
    """
    db = db_session_factory()
    _element(db, "ME-1", {"101": _cell(["102", "201", "999"], group="S1", zone="Z1"),                 # 102: same element; 201: another; 999: unmanaged
                          "102": _cell(["101"], group="S1", zone="Z1")})
    _element(db, "ME-2", {"201": _cell(["101"], group="S2", zone="Z1"), "202": _cell(["301"])})
    _element(db, "ME-3", {"301": _cell([])})
    _element(db, "ME-4", {"301": _cell([])})                                                           # 301 is claimed twice
    db.commit()
    db.close()


def _links(client, **params):
    return client.get("/topology/links", params=params).json()["items"]


def _by_pair(items):
    return {(i["aCell"], i["bCell"]): i for i in items}


def test_each_declared_relation_has_a_type(client, network):
    """Each declared neighbour relation is typed INTRA_ELEMENT, INTER_ELEMENT, EXTERNAL (nobody claims it) or AMBIGUOUS (several do, and the other element is not guessed).
    """
    links = _by_pair(_links(client))
    assert links[("101", "102")]["linkType"] == "INTRA_ELEMENT"
    assert links[("101", "201")]["linkType"] == "INTER_ELEMENT"
    assert links[("101", "999")]["linkType"] == "EXTERNAL" and links[("101", "999")]["bElement"] is None
    assert links[("202", "301")]["linkType"] == "AMBIGUOUS" and links[("202", "301")]["bElement"] is None     # not guessed


def test_a_relation_says_whether_the_other_side_declares_it_back(client, network):
    """`reciprocal` says whether the other cell lists this one as a neighbour too."""
    links = _by_pair(_links(client))
    assert links[("101", "102")]["reciprocal"] is True and links[("101", "201")]["reciprocal"] is True
    assert links[("202", "301")]["reciprocal"] is False and links[("101", "999")]["reciprocal"] is False


def test_the_sector_group_and_the_incident_zone_are_compared(client, network):
    """Each link says whether its cells share a sector group and an incident zone; with no other side there is nothing to compare, which is not 'same'.
    """
    links = _by_pair(_links(client))
    assert (links[("101", "102")]["sameSectorGroup"], links[("101", "102")]["sameIncidentZone"]) == (True, True)
    assert (links[("101", "201")]["sameSectorGroup"], links[("101", "201")]["sameIncidentZone"]) == (False, True)
    assert links[("101", "999")]["sameSectorGroup"] is False                                          # nothing to compare is not "same"


def test_the_links_can_be_narrowed_by_element_and_by_type(client, network):
    """The list can be narrowed by link type, and by element at either end; an unknown link type is a 422."""
    assert {i["linkType"] for i in _links(client, link_type="INTER_ELEMENT")} == {"INTER_ELEMENT"}
    assert {(i["aCell"], i["bCell"]) for i in _links(client, link_type="INTER_ELEMENT")} == {("101", "201"), ("201", "101")}
    ends = _links(client, managed_element_ref="ME-2")                                                  # at either end
    assert {(i["aCell"], i["bCell"]) for i in ends} == {("101", "201"), ("201", "101"), ("202", "301")}
    assert client.get("/topology/links", params={"link_type": "FRONTHAUL"}).status_code == 422


def test_the_links_page_only_when_asked(client, network):
    """PR-GUI-9.4: without `limit`/`offset` the answer is the old `{items}`; with either it is the page envelope over the same order."""
    whole = client.get("/topology/links").json()
    assert set(whole) == {"items"} and len(whole["items"]) == 6
    page = client.get("/topology/links", params={"limit": 4}).json()
    assert page["total"] == 6 and page["limit"] == 4 and page["offset"] == 0 and page["items"] == whole["items"][:4]
    assert client.get("/topology/links", params={"offset": 4}).json()["items"] == whole["items"][4:]


def test_the_links_can_be_narrowed_by_reciprocity(client, network):
    """PR-GUI-9.4: `reciprocal=false` lists the one-sided relations (the problem table of the topology page), `true` the others."""
    assert {(i["aCell"], i["bCell"]) for i in _links(client, reciprocal="false")} == {("101", "999"), ("202", "301")}
    assert len(_links(client, reciprocal="true")) == 4


def test_the_link_counts(client, network):
    """PR-GUI-9.4: the counts of the topology page's tiles, without the list; narrowed by element like the list."""
    assert client.get("/topology/links/counts").json() == {"total": 6, "notReciprocal": 2, "external": 1, "ambiguous": 1,
                                                          "intraElement": 2, "interElement": 2}
    assert client.get("/topology/links/counts", params={"managed_element_ref": "ME-3"}).json()["total"] == 0


def test_a_cell_is_not_its_own_neighbour(client, db_session_factory):
    """A cell that lists itself as a neighbour gives no link."""
    db = db_session_factory()
    _element(db, "ME-1", {"101": _cell(["101"])})
    db.commit()
    db.close()
    assert _links(client) == []


def test_no_cells_no_links(client):
    """With no cells registered the list is empty."""
    assert _links(client) == []


# ---- containment ----

DNS = {"root": "ManagedElement=ME-1", "du": "ManagedElement=ME-1,GNBDUFunction=1", "c1": "ManagedElement=ME-1,GNBDUFunction=1,NRCellDU=101",
       "c2": "ManagedElement=ME-1,GNBDUFunction=1,NRCellDU=102", "cu": "ManagedElement=ME-1,GNBCUUPFunction=1", "other": "ManagedElement=ME-2"}


@pytest.fixture
def tree(db_session_factory):
    """Fixture: a small containment tree for ME-1 (root, a DU function with two cells, a CU-UP function) and a root of ME-2, keyed by the `DNS` map.
    """
    db = db_session_factory()
    for ref in ("ME-1", "ME-2"):
        db.add(ManagedEntity(managed_element_ref=ref, entity_type="O-DU", o1_protocol="NETCONF", cell_guards={}))
    db.flush()
    rows = [("root", None, "ManagedElement", "ME-1", "ME-1"), ("du", "root", "GNBDUFunction", "1", "ME-1"), ("c1", "du", "NRCellDU", "101", "ME-1"),
            ("c2", "du", "NRCellDU", "102", "ME-1"), ("cu", "root", "GNBCUUPFunction", "1", "ME-1"), ("other", None, "ManagedElement", "ME-2", "ME-2")]
    for key, parent, cls, oid, element in rows:
        db.add(ManagedObject(dn=DNS[key], parent_dn=DNS[parent] if parent else None, object_class=cls, object_id=oid, managed_element_ref=element, source="walk"))
        db.flush()
    db.commit()
    db.close()


@pytest.mark.parametrize("a, b, relation", [
    ("c1", "c1", "SAME"), ("du", "c1", "ANCESTOR"), ("root", "c2", "ANCESTOR"), ("c1", "du", "DESCENDANT"),
    ("c1", "c2", "SIBLING"), ("c1", "cu", "SAME_ELEMENT"), ("du", "cu", "SIBLING"), ("c1", "other", "DIFFERENT_ELEMENT"),
])
def test_how_two_managed_objects_are_related(client, tree, a, b, relation):
    """The relation of two DNs is SAME, ANCESTOR, DESCENDANT, SIBLING, SAME_ELEMENT or DIFFERENT_ELEMENT as the table says."""
    resp = client.get("/topology/relation", params={"a": DNS[a], "b": DNS[b]})
    assert resp.status_code == 200 and resp.json() == {"a": DNS[a], "b": DNS[b], "relation": relation}


def test_a_dn_that_is_not_in_the_tree_is_404(client, tree):
    """A relation involving a DN that is not in the tree is 404 MANAGED_OBJECT_NOT_FOUND."""
    resp = client.get("/topology/relation", params={"a": DNS["c1"], "b": "ManagedElement=GHOST"})
    assert resp.status_code == 404 and resp.json()["detail"]["title"] == "MANAGED_OBJECT_NOT_FOUND"
