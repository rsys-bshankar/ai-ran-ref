"""W10-alarm-cellref: which cells a managed element's critical alarms hold.
Run with: pytest smo/sdk/tests -q
"""

import pytest

from smo_sdk.data import AlarmScope, DataClient, alarm_cell


@pytest.fixture
def client(r1):
    return DataClient(r1)


def _alarm(alarm_id, ref=None, severity="critical"):
    return {"alarmId": alarm_id, "managedFunctionRef": ref, "severity": severity}


@pytest.mark.parametrize("ref, cell", [
    ("NRCellDU=101", "101"), ("NRCellCU=7", "7"), ("NRSectorCarrier=101", "101"),
    ("CommonBeamformingFunction=12", "12"), ("CESManagementFunction=101", "101"),
    ("NRCellRelation=101-102", "101"), ("NRFreqRelation=101-L2", "101"),
    (None, None), ("", None), ("DMROFunction=1", None), ("NRCellDU", None), ("NRCellDU=", None),
])
def test_the_cell_an_alarm_is_about(ref, cell):
    assert alarm_cell(_alarm("a", ref)) == cell


def test_an_alarm_on_a_cell_holds_that_cell_only():
    scope = AlarmScope([_alarm("a1", "NRCellDU=101")])
    assert scope.ids_holding(["101"]) == ["a1"]
    assert scope.ids_holding(["102", "103"]) == []
    assert scope.ids_holding(["102", "101"]) == ["a1"]   # a neighbour counts when the caller says so


def test_an_alarm_naming_no_cell_holds_every_cell():
    scope = AlarmScope([_alarm("whole"), _alarm("a1", "NRCellDU=101")])
    assert scope.ids_holding(["999"]) == ["whole"]
    assert scope.ids_holding(["101"]) == ["whole", "a1"]


def test_only_critical_alarms_hold():
    assert AlarmScope([_alarm("m", severity="major"), _alarm("c", severity="cleared")]).holding(["101"]) == []


def test_query_critical_alarms(client, r1):
    r1.script(200, {"items": [_alarm("a1", "NRCellDU=101")], "total": 1, "limit": 500, "offset": 0})
    scope = client.query_critical_alarms("gnb-1")
    assert r1.calls[0]["path"] == "/ran-nf-oam/alarms"
    assert r1.calls[0]["params"] == {"managed_element_ref": "gnb-1", "severity": "critical", "limit": 500}
    assert scope.ids_holding(["101"]) == ["a1"]
