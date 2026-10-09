"""Tests of which cells a managed element's critical alarms hold: `alarm_cell`, `AlarmScope` and `DataClient.query_critical_alarms` (HISTORY.md W10-alarm-cellref).

Run with `cd sdk && PYTHONPATH=.:../shared python -m pytest tests/test_alarm_scope.py -q`. Uses the `r1` fixture of `conftest.py` (a recording fake `R1Client`); no network.
"""

import pytest

from smo_sdk.data import AlarmScope, DataClient, alarm_cell


@pytest.fixture
def client(r1):
    """A `DataClient` over the recording fake."""
    return DataClient(r1)


def _alarm(alarm_id, ref=None, severity="critical"):
    """An alarm dict with the three fields the scope reads: id, managedFunctionRef and severity (critical by default)."""
    return {"alarmId": alarm_id, "managedFunctionRef": ref, "severity": severity}


@pytest.mark.parametrize("ref, cell", [
    ("NRCellDU=101", "101"), ("NRCellCU=7", "7"), ("NRSectorCarrier=101", "101"),
    ("CommonBeamformingFunction=12", "12"), ("CESManagementFunction=101", "101"),
    ("NRCellRelation=101-102", "101"), ("NRFreqRelation=101-L2", "101"),
    (None, None), ("", None), ("DMROFunction=1", None), ("NRCellDU", None), ("NRCellDU=", None),
])
def test_the_cell_an_alarm_is_about(ref, cell):
    """Each cell or relation IOC maps to its cell id, and a missing, empty, malformed or element-wide (`DMROFunction`) ref maps to None."""
    assert alarm_cell(_alarm("a", ref)) == cell


def test_an_alarm_on_a_cell_holds_that_cell_only():
    """An alarm on cell 101 holds 101 and not other cells; a neighbour counts only when the caller passes it."""
    scope = AlarmScope([_alarm("a1", "NRCellDU=101")])
    assert scope.ids_holding(["101"]) == ["a1"]
    assert scope.ids_holding(["102", "103"]) == []
    assert scope.ids_holding(["102", "101"]) == ["a1"]   # a neighbour counts when the caller says so


def test_an_alarm_naming_no_cell_holds_every_cell():
    """An alarm that names no cell holds every cell, so a cell is never judged free while an element-wide critical alarm is active."""
    scope = AlarmScope([_alarm("whole"), _alarm("a1", "NRCellDU=101")])
    assert scope.ids_holding(["999"]) == ["whole"]
    assert scope.ids_holding(["101"]) == ["whole", "a1"]


def test_only_critical_alarms_hold():
    """An alarm that is not critical (major, cleared) holds nothing."""
    assert AlarmScope([_alarm("m", severity="major"), _alarm("c", severity="cleared")]).holding(["101"]) == []


def test_query_critical_alarms(client, r1):
    """`query_critical_alarms` asks RAN NF OAM for the element's critical alarms (limit 500), unwraps the page and returns a scope that answers per cell."""
    r1.script(200, {"items": [_alarm("a1", "NRCellDU=101")], "total": 1, "limit": 500, "offset": 0})
    scope = client.query_critical_alarms("gnb-1")
    assert r1.calls[0]["path"] == "/ran-nf-oam/alarms"
    assert r1.calls[0]["params"] == {"managed_element_ref": "gnb-1", "severity": "critical", "limit": 500}
    assert scope.ids_holding(["101"]) == ["a1"]
