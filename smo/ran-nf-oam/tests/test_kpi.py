"""PR-MGT-11.1, 11.3-11.5: KPI definitions, and computing a KPI per cell, element or region from stored PM files."""

import datetime
import json

import pytest

from test_main import client, db_session_factory  # noqa: F401  (pytest fixtures)

from app.models import ManagedEntity, PMFile

T0 = datetime.datetime(2026, 10, 1, 12, 0, tzinfo=datetime.UTC)


def _file(db_session_factory, element, counter_type, measurements):
    db = db_session_factory()
    content = json.dumps({"managedElementRef": element, "counterType": counter_type, "measurements": measurements})
    db.add(PMFile(managed_element_ref=element, counter_type=counter_type, content=content, file_size=len(content)))
    db.commit()
    db.close()


def _m(cell, minute, value=None, **values):
    m = {"cellId": cell, "timestamp": (T0 + datetime.timedelta(minutes=minute)).isoformat()}
    if value is not None:
        m["value"] = value
    if values:
        m["values"] = values
    return m


@pytest.fixture
def pm(db_session_factory):
    """Two elements, cells 1 and 2 on ME-A (sector group north) and cell 3 on ME-B (south); handover attempts and failures, PRB utilisation."""
    db = db_session_factory()
    db.add(ManagedEntity(managed_element_ref="ME-A", entity_type="O-DU", o1_protocol="NETCONF",
                         cell_guards={"1": {"sectorGroup": "north"}, "2": {"sectorGroup": "north", "incidentZone": "z1"}}))
    db.add(ManagedEntity(managed_element_ref="ME-B", entity_type="O-DU", o1_protocol="NETCONF", cell_guards={"3": {"sectorGroup": "south"}}))
    db.commit()
    db.close()
    _file(db_session_factory, "ME-A", "HO", [_m("1", 0, **{"MM.HoExeAtt": 100, "MM.HoFail": 10}), _m("1", 15, **{"MM.HoExeAtt": 100, "MM.HoFail": 30}),
                                              _m("2", 0, **{"MM.HoExeAtt": 50, "MM.HoFail": 0})])
    _file(db_session_factory, "ME-B", "HO", [_m("3", 5, **{"MM.HoExeAtt": 200, "MM.HoFail": 20})])
    _file(db_session_factory, "ME-A", "PRB_UTILIZATION", [_m("1", 0, 40.0), _m("1", 15, 60.0), _m("2", 0, 20.0)])


HO_SUCCESS = {"formula": "100 * (att - fail) / att", "unit": "%", "counters": [
    {"counter": "MM.HoExeAtt", "variable": "att", "aggregation": "sum"}, {"counter": "MM.HoFail", "variable": "fail", "aggregation": "sum"}]}


def _query(client, name, **params):
    base = {"from_time": T0.isoformat(), "to_time": (T0 + datetime.timedelta(hours=1)).isoformat()}
    return client.get(f"/kpis/{name}", params={**base, **params})


def test_a_definition_is_stored_listed_read_replaced_and_deleted(client):
    assert client.put("/kpi-definitions/ho_success", json=HO_SUCCESS).status_code == 200
    assert client.get("/kpi-definitions/ho_success").json()["counters"][0] == {"counter": "MM.HoExeAtt", "variable": "att", "aggregation": "sum"}
    assert [d["name"] for d in client.get("/kpi-definitions").json()["items"]] == ["ho_success"]
    assert client.put("/kpi-definitions/ho_success", json={**HO_SUCCESS, "description": "v2"}).json()["description"] == "v2"
    assert client.delete("/kpi-definitions/ho_success").status_code == 204
    assert client.get("/kpi-definitions/ho_success").status_code == 404 and "KPI_NOT_FOUND" in client.get("/kpi-definitions/ho_success").text


def test_counters_default_to_the_formulas_variables_summed(client):
    row = client.put("/kpi-definitions/ratio", json={"formula": "100 * ok / total"}).json()
    assert row["counters"] == [{"counter": "ok", "variable": "ok", "aggregation": "sum"}, {"counter": "total", "variable": "total", "aggregation": "sum"}]


@pytest.mark.parametrize("body", [
    {"formula": "__import__('os').system('id')"},
    {"formula": "a +"},
    {"formula": "a / b", "counters": [{"counter": "A", "variable": "a"}]},                       # b is fed by nothing
    {"formula": "a", "counters": [{"counter": "A", "variable": "a"}, {"counter": "B", "variable": "b"}]},   # b is unused
    {"formula": "a", "counters": [{"counter": "A", "variable": "a"}, {"counter": "B", "variable": "a"}]},   # a declared twice
    {"formula": "a", "counters": [{"counter": "A", "variable": "not an identifier"}]},
    {"formula": "a", "counters": [{"counter": "A", "variable": "a", "aggregation": "median"}]},
])
def test_a_definition_that_is_not_acceptable_is_refused(client, body):
    assert client.put("/kpi-definitions/bad", json=body).status_code == 422
    assert client.get("/kpi-definitions/bad").status_code == 404


def test_a_kpi_name_is_validated(client):
    assert client.put("/kpi-definitions/1bad", json={"formula": "a"}).status_code == 422


def test_a_kpi_is_computed_per_cell_from_the_counters_summed_over_the_period(client, pm):
    client.put("/kpi-definitions/ho_success", json=HO_SUCCESS)
    items = {(i["group"]["managedElementRef"], i["group"]["cellId"]): i for i in _query(client, "ho_success").json()["items"]}
    assert items[("ME-A", "1")]["value"] == 80.0 and items[("ME-A", "1")]["counters"] == {"att": 200.0, "fail": 40.0}
    assert items[("ME-A", "2")]["value"] == 100.0 and items[("ME-B", "3")]["value"] == 90.0 and items[("ME-A", "1")]["samples"] == 4


def test_the_period_is_half_open_and_filters_apply(client, pm):
    client.put("/kpi-definitions/ho_success", json=HO_SUCCESS)
    early = _query(client, "ho_success", to_time=(T0 + datetime.timedelta(minutes=15)).isoformat(), cell_id="1").json()["items"]
    assert len(early) == 1 and early[0]["value"] == 90.0                                       # the sample at minute 15 is not in [t0, t0+15)
    assert [i["group"]["cellId"] for i in _query(client, "ho_success", managed_element_ref="ME-B").json()["items"]] == ["3"]
    assert _query(client, "ho_success", from_time=(T0 + datetime.timedelta(days=1)).isoformat(),
                  to_time=(T0 + datetime.timedelta(days=2)).isoformat()).json()["items"] == []


def test_a_regional_ratio_is_made_from_the_regions_summed_counters_not_the_mean_of_the_cell_ratios(client, pm):
    client.put("/kpi-definitions/ho_success", json=HO_SUCCESS)
    north = {i["group"]["sectorGroup"]: i for i in _query(client, "ho_success", group_by="sectorGroup").json()["items"]}
    assert north["north"]["counters"] == {"att": 250.0, "fail": 40.0} and north["north"]["value"] == 84.0     # not (80 + 100) / 2 = 90
    assert north["south"]["value"] == 90.0
    zones = {i["group"]["incidentZone"] for i in _query(client, "ho_success", group_by="incidentZone").json()["items"]}
    assert zones == {"z1", "unassigned"}


def test_group_by_element_and_all(client, pm):
    client.put("/kpi-definitions/ho_success", json=HO_SUCCESS)
    by_element = {i["group"]["managedElementRef"]: i["value"] for i in _query(client, "ho_success", group_by="element").json()["items"]}
    assert by_element == {"ME-A": 84.0, "ME-B": 90.0}
    everything = _query(client, "ho_success", group_by="all").json()["items"]
    assert len(everything) == 1 and everything[0]["group"] == {} and everything[0]["counters"] == {"att": 450.0, "fail": 60.0}


def test_each_counter_is_combined_as_its_definition_says(client, pm):
    client.put("/kpi-definitions/prb", json={"formula": "avg_util", "counters": [{"counter": "PRB_UTILIZATION", "variable": "avg_util", "aggregation": "avg"}]})
    client.put("/kpi-definitions/prb_last", json={"formula": "u", "counters": [{"counter": "PRB_UTILIZATION", "variable": "u", "aggregation": "last"}]})
    client.put("/kpi-definitions/prb_peak", json={"formula": "u", "counters": [{"counter": "PRB_UTILIZATION", "variable": "u", "aggregation": "max"}]})
    client.put("/kpi-definitions/prb_n", json={"formula": "u", "counters": [{"counter": "PRB_UTILIZATION", "variable": "u", "aggregation": "count"}]})
    one = lambda name: _query(client, name, cell_id="1").json()["items"][0]["value"]  # noqa: E731
    assert (one("prb"), one("prb_last"), one("prb_peak"), one("prb_n")) == (50.0, 60.0, 60.0, 2.0)


def test_a_kpi_over_two_counter_families_reads_both(client, pm):
    client.put("/kpi-definitions/mix", json={"formula": "att / util", "counters": [
        {"counter": "MM.HoExeAtt", "variable": "att"}, {"counter": "PRB_UTILIZATION", "variable": "util", "aggregation": "avg"}]})
    items = {i["group"]["cellId"]: i for i in _query(client, "mix", managed_element_ref="ME-A").json()["items"]}
    assert items["1"]["value"] == 4.0 and items["2"]["value"] == 2.5


def test_no_data_and_undefined_are_told_apart(client, pm, db_session_factory):
    client.put("/kpi-definitions/ho_success", json=HO_SUCCESS)
    _file(db_session_factory, "ME-C", "HO", [_m("9", 1, **{"MM.HoExeAtt": 0, "MM.HoFail": 0})])           # no handovers at all: 0 / 0
    _file(db_session_factory, "ME-C", "HO", [_m("8", 1, **{"MM.HoExeAtt": 5})])                           # no failure counter
    items = {i["group"]["cellId"]: i for i in _query(client, "ho_success", managed_element_ref="ME-C").json()["items"]}
    assert items["9"]["value"] is None and items["9"]["reason"] == "UNDEFINED"
    assert items["8"]["value"] is None and items["8"]["reason"] == "NO_DATA"
    nothing = _query(client, "ho_success", group_by="all", managed_element_ref="ME-ZZ").json()["items"]
    assert nothing == [{"group": {}, "value": None, "samples": 0, "counters": {"att": None, "fail": None}, "reason": "NO_DATA"}]


def test_a_kpi_that_does_not_exist_and_a_bad_period(client, pm):
    assert _query(client, "nope").status_code == 404
    client.put("/kpi-definitions/ho_success", json=HO_SUCCESS)
    assert _query(client, "ho_success", to_time=(T0 - datetime.timedelta(hours=1)).isoformat()).status_code == 422
    assert _query(client, "ho_success", group_by="planet").status_code == 422


def test_more_files_than_the_bound_is_said(client, pm, monkeypatch):
    client.put("/kpi-definitions/ho_success", json=HO_SUCCESS)
    monkeypatch.setattr("app.kpi.MAX_FILES", 1)
    body = _query(client, "ho_success").json()
    assert body["truncated"] is True and body["filesScanned"] == 1
