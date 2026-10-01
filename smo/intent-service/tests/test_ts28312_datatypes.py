"""SA-INTENT-partial: the value datatypes of TS 28.312 (Frequency, UEGroup, QoSId,
CivicArea, CivicAddress, ReportingCondition, TimeCondition, TargetFulfilmentCondition)
and ValueRangeType, validated by structure. Run with: pytest smo/intent-service/tests -q
"""

import pytest

from test_main import (  # noqa: F401  (pytest fixtures and helpers)
    _capability, _expectation, _intent, _register_rmih, client,
)

from app.ts28312_datatypes import named_datatype_problem, reporting_condition_problem, value_range_problem

# ---------------------------------------------------------------- the datatypes

GOOD = {
    "UEGroup": [{"pLMNId": {"mcc": "262", "mnc": "01"}}, {"qOSId": {"fiveQI": 9}}, {"qOSId": {"qCI": 7}},
                {"sNssai": {"sst": 1, "sd": "0A0B0C"}}, {"uEType": "REDCAP_UE"},
                {"pLMNId": {"mcc": "310", "mnc": "260"}, "qOSId": {"fiveQI": 1}, "sNssai": {"sst": 2}}],
    "DlFrequency": [{"arfcn": 632628}, {"freqband": "n78"}, {"arfcn": 632628, "freqband": "n78"}],
    "CivicArea": [{"locationLabel": "Hotel Adlon"}, {"civicAddress": {"country": "DE", "A3": "Berlin", "RD": "Unter den Linden", "HNO": "77"}}],
    "schedulingTime": [{"startTime": "2026-01-01T00:00:00Z", "endTime": "2026-01-02T00:00:00+01:00"},
                       {"timeIntervals": [{"intervalStart": "00:00:00Z", "intervalEnd": "05:00:00Z"}]},
                       {"daysOfWeek": ["MONDAY", "FRIDAY"], "timeIntervals": [{"intervalStart": "08:00:00+02:00", "intervalEnd": "17:00:00+02:00"}]},
                       {"daysOfMonth": [1, 15, 31], "timeIntervals": [{"intervalStart": "00:00:00Z", "intervalEnd": "01:00:00Z"}]}],
}
BAD = {
    "UEGroup": [{}, {"qOSId": {"qCI": 7, "fiveQI": 9}}, {"qOSId": {}}, {"qOSId": {"fiveQI": 256}}, {"pLMNId": {"mcc": "26", "mnc": "01"}},
                {"pLMNId": {"mcc": "262", "mnc": "1"}}, {"sNssai": {"sd": "XYZ"}}, {"sNssai": {"sst": 256}}, {"uEType": "LAPTOP"}, {"bogus": 1}],
    "DlFrequency": [{}, {"arfcn": "high"}, {"freqband": 78}, {"band": "n78"}],
    "CivicArea": [{}, {"locationLabel": "x", "civicAddress": {"country": "DE"}}, {"civicAddress": {"country": 49}}, {"civicAddress": {"street": "x"}}],
    "schedulingTime": [{}, {"startTime": "yesterday"}, {"timeIntervals": [{"intervalStart": "5am"}]}, {"daysOfWeek": ["FUNDAY"], "timeIntervals": []},
                       {"daysOfWeek": ["MONDAY", "MONDAY"]}, {"daysOfMonth": [32]}, {"daysOfWeek": ["MONDAY"], "daysOfMonth": [1]},
                       {"startTime": "2026-01-01T00:00:00Z", "timeIntervals": []}, {"timeWindow": {}, "recurrencePattern": "DAILY"}],
}


@pytest.mark.parametrize("name", GOOD)
def test_well_formed_values_pass_and_a_list_of_them_too(name):
    for value in GOOD[name]:
        assert named_datatype_problem(name, value) is None, value
    assert named_datatype_problem(name, GOOD[name]) is None


@pytest.mark.parametrize("name", BAD)
def test_malformed_values_are_refused(name):
    for value in BAD[name]:
        assert named_datatype_problem(name, value), value
    assert named_datatype_problem(name, [GOOD[name][0], BAD[name][0]])  # one bad item in a list
    assert named_datatype_problem(name, "a string")


def test_ul_frequency_is_a_frequency_too():
    assert named_datatype_problem("UlFrequency", {"arfcn": 100}) is None and named_datatype_problem("UlFrequency", {}) is not None
    assert named_datatype_problem("NotADatatype", object()) is None  # other names are not this module's business


# ---------------------------------------------------------------- ValueRangeType

def test_value_range_type_accepts_scalars_lists_and_the_structured_forms():
    for value in (1, 2.5, "LOCKED", True, None, ["a", 1], [], {"mcc": "262", "mnc": "01"}, {"latitude": 48.1, "longitude": 11.5},
                  {"geoCircle": {"distanceRadius": 500, "referenceLocation": {"latitude": 1, "longitude": 2}}},
                  {"geoPolygon": [{"latitude": 1, "longitude": 2}]}, {"startTime": "2026-01-01T00:00:00Z"},
                  {"arfcn": 1}, {"sNssai": {"sst": 1}}, {"locationLabel": "x"}, [{"arfcn": 1}, {"freqband": "n1"}]):
        assert value_range_problem(value) is None, value


def test_value_range_type_refuses_everything_else():
    for value in ({"nCI": 101}, {"latitude": 99}, {"geoPolygon": []}, {"geoCircle": {"distanceRadius": 0}}, {"mcc": "26"},
                  {"anything": "else"}, [{"nCI": 1}], {"arfcn": 1, "bogus": 2}, object()):
        assert value_range_problem(value), value
    assert "matches no ValueRangeType form" in value_range_problem({"nCI": 101})


# ---------------------------------------------------------------- ReportingCondition

def test_reporting_conditions_are_a_time_condition_or_a_target_fulfilment_condition():
    time_condition = {"timeIntervals": [{"intervalStart": "00:00:00Z", "intervalEnd": "06:00:00Z"}]}
    target = {"targetName": "AveDLPrbLoad", "targetCondition": "IS_GREATER_THAN", "targetValueRange": 80}
    assert reporting_condition_problem(time_condition) is None and reporting_condition_problem(target) is None
    assert reporting_condition_problem({**target, "targetValueRange": [10, {"mcc": "262"}]}) is None
    for bad in ({}, {"targetName": "x"}, {**target, "targetCondition": "IS_SORT_OF"}, {**target, "targetValueRange": {"nCI": 1}},
                {**target, "extra": 1}, {"daysOfWeek": ["NOPE"]}, "not an object"):
        assert reporting_condition_problem(bad), bad
    assert reporting_condition_problem({"targetName": "x"}).startswith("neither a TimeCondition")


# ---------------------------------------------------------------- through the API

def _post(client, expectation=None, **extra):
    return client.post("/intents", json=_intent(expectations=[expectation or _expectation()], **extra))


def test_a_ueg_group_context_is_checked_on_creation(client):
    _register_rmih(client)
    for value, status in (([{"sNssai": {"sst": 1}}], 201), ([{"sNssai": {"sst": 999}}], 422), ([{"qOSId": {}}], 422)):
        exp = _expectation()
        exp["expectationObject"]["objectContexts"] = [{"contextAttribute": "UEGroup", "contextCondition": "IS_ALL_OF", "contextValueRange": value}]
        assert _post(client, exp).status_code == status, value


def test_frequency_and_civic_area_contexts_are_checked(client):
    _register_rmih(client)
    cases = (("DlFrequency", [{"arfcn": 632628}], 201), ("DlFrequency", [{"arfcn": "x"}], 422),
             ("UlFrequency", [{"freqband": "n78"}], 201), ("CivicArea", [{"locationLabel": "Hotel"}], 201),
             ("CivicArea", [{"civicAddress": {"country": 1}}], 422))
    for name, value, status in cases:
        exp = _expectation()
        exp["expectationObject"]["objectContexts"] = [{"contextAttribute": name, "contextCondition": "IS_ALL_OF", "contextValueRange": value}]
        assert _post(client, exp).status_code == status, (name, value)


def test_a_scheduling_time_guarantee_period_is_checked(client):
    _register_rmih(client)

    def period(value):
        exp = _expectation()
        exp["guaranteePeriods"] = [{"contextAttribute": "schedulingTime", "contextCondition": "IS_ALL_OF", "contextValueRange": value}]
        return _post(client, exp).status_code

    assert period({"timeIntervals": [{"intervalStart": "00:00:00Z", "intervalEnd": "05:00:00Z"}]}) == 201
    assert period({"timeWindow": {"startTime": "00:00", "endTime": "05:00"}, "recurrencePattern": "DAILY"}) == 422  # the old SDK shape


def test_a_generic_target_or_context_value_is_a_value_range_type(client):
    _register_rmih(client, capabilities=[_capability(target_names=["MyVendorTarget"])])
    for value, status in (("x", 201), ([1, 2], 201), ({"mcc": "262", "mnc": "01"}, 201), ({"nCI": 101}, 422)):
        target = {"targetName": "MyVendorTarget", "targetCondition": "IS_EQUAL_TO", "targetValueRange": value}
        assert _post(client, _expectation(targets=[target])).status_code == status, value
    exp = _expectation()
    exp["expectationContexts"] = [{"contextAttribute": "VendorContext", "contextCondition": "IS_EQUAL_TO", "contextValueRange": {"x": 1}}]
    assert _post(client, exp).status_code == 422


def test_reporting_conditions_are_checked_in_intent_report_control(client):
    _register_rmih(client)

    def create(conditions):
        control = [{"observationPeriod": 60, "reportingConditions": conditions}]
        return client.post("/intents", json=_intent(intentReportControl=control)).status_code

    good = [{"timeIntervals": [{"intervalStart": "00:00:00Z", "intervalEnd": "01:00:00Z"}]},
            {"targetName": "AveDLPrbLoad", "targetCondition": "IS_GREATER_THAN", "targetValueRange": 90}]
    assert create(good) == 201
    assert create([{"targetName": "AveDLPrbLoad"}]) == 422
    assert create([{"daysOfMonth": [40]}]) == 422
