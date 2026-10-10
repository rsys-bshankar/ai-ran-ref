"""The structured value datatypes of TS 28.312 that `ValueRangeType` and `ReportingCondition` use, as strict pydantic models plus the functions that check a
value against them (SA-INTENT-partial).

What it is: `Frequency`, `UEGroup` (with `QoSId`, `PlmnId`, `Snssai`), `CivicArea` / `CivicAddress`, `SchedulingTime` (with `TimeWindow`,
`TimeInterval`), `GeoArea` (with `GeoCircle`, `GeoCoordinate`) and `TargetFulfilmentCondition` from `TS28312_IntentNrm.yaml`, TS 28.541 and
TS 28.623, and three checking functions: `value_range_problem`, `named_datatype_problem` and `reporting_condition_problem`. Design record:
`intent-service/README.md` (1.2).

`value_range_problem(value)` is the whole `ValueRangeType` rule: a scalar is fine; a list is each of its items; an object must match at least one
of the structured alternatives in `STRUCTURED`. `None` is allowed (an optional value that is absent).

Where it sits: imported by `ts28312.py`, which calls these functions from the validators of `IntentExpectation` and `IntentReportControl`; nothing
here touches the database or HTTP. Each checking function returns a reason string, or None when the value is fine; the caller turns a reason into
a `ValueError`, so the API answers 422.

Recorded deviation: `DateTime` and `FullTime` strings are checked by pattern (RFC 3339 shape), not by calendar validity, and a `GeoArea` polygon
is not checked for closure.

Before editing: every model forbids unknown keys, which is what keeps the alternatives of `ValueRangeType` apart (an object that fits two forms
would otherwise be ambiguous); loosening one model changes what the others accept.
"""

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

_DATE_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$")
_FULL_TIME = re.compile(r"^\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$")


# Base of the datatype models in this file: unknown keys are refused.
class _S(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _one_key(value: dict, allowed: tuple[str, ...], what: str) -> None:
    """Raises `ValueError` unless `value` has exactly one key and it is one of `allowed`; `what` names the datatype in the message.

    No code in the repository calls it.
    """
    present = [k for k in allowed if k in value]
    if len(present) != 1 or len(value) != 1:
        raise ValueError(f"{what} takes exactly one of {', '.join(allowed)}")


class QoSId(_S):
    """`qCI` (EUTRAN) or `fiveQI` (NR), 0..255; exactly one."""
    qCI: int | None = Field(default=None, ge=0, le=255)
    fiveQI: int | None = Field(default=None, ge=0, le=255)

    @model_validator(mode="after")
    def _one(self):
        if (self.qCI is None) == (self.fiveQI is None):
            raise ValueError("QoSId takes exactly one of qCI, fiveQI")
        return self


# TS 28.541 PlmnId: `mcc` is three digits, `mnc` two or three; both optional here, as an empty object matches (a `UEGroup` needs at least one part).
class PlmnId(_S):
    mcc: str | None = Field(default=None, pattern=r"^[0-9]{3}$")
    mnc: str | None = Field(default=None, pattern=r"^[0-9]{2,3}$")


# TS 28.541 Snssai: `sst` 0..255 and `sd` six hex digits; both optional.
class Snssai(_S):
    sst: int | None = Field(default=None, ge=0, le=255)
    sd: str | None = Field(default=None, pattern=r"^[A-Fa-f0-9]{6}$")


# TS 28.312 UEGroup: at least one of PLMN, QoS, S-NSSAI or UE type; `uEType` is REDCAP_UE or EREDCAP_UE.
class UEGroup(_S):
    pLMNId: PlmnId | None = None
    qOSId: QoSId | None = None
    sNssai: Snssai | None = None
    uEType: str | None = None

    @field_validator("uEType")
    @classmethod
    def _ue_type(cls, v):
        if v is not None and v not in ("REDCAP_UE", "EREDCAP_UE"):
            raise ValueError("uEType must be REDCAP_UE or EREDCAP_UE")
        return v

    @model_validator(mode="after")
    def _some(self):
        if all(v is None for v in (self.pLMNId, self.qOSId, self.sNssai, self.uEType)):
            raise ValueError("a UEGroup names a PLMN, QoS, S-NSSAI or UE type")
        return self


# TS 28.312 Frequency: an `arfcn`, a `freqband` or both.
class Frequency(_S):
    arfcn: int | None = None
    freqband: str | None = None

    @model_validator(mode="after")
    def _some(self):
        if self.arfcn is None and self.freqband is None:
            raise ValueError("a Frequency carries an arfcn or a freqband")
        return self


# RFC 4119 / TS 28.312 CivicAddress: every field is an optional string, with the spec's own key names (A1..A6, HNO, ...), and unknown keys are refused.
class CivicAddress(_S):
    A1: str | None = None
    A2: str | None = None
    A3: str | None = None
    A4: str | None = None
    A5: str | None = None
    A6: str | None = None
    ADDCODE: str | None = None
    BLD: str | None = None
    FLR: str | None = None
    HNO: str | None = None
    HNS: str | None = None
    LMK: str | None = None
    LOC: str | None = None
    NAM: str | None = None
    PC: str | None = None
    PCN: str | None = None
    PLC: str | None = None
    POBOX: str | None = None
    POD: str | None = None
    POM: str | None = None
    PRD: str | None = None
    PRM: str | None = None
    RD: str | None = None
    RDBR: str | None = None
    RDSEC: str | None = None
    RDSUBBR: str | None = None
    ROOM: str | None = None
    SEAT: str | None = None
    STS: str | None = None
    UNIT: str | None = None
    country: str | None = None
    method: str | None = None
    providedBy: str | None = None
    usageRules: str | None = None


# TS 28.312 CivicArea: exactly one of `civicAddress` or `locationLabel`.
class CivicArea(_S):
    civicAddress: CivicAddress | None = None
    locationLabel: str | None = None

    @model_validator(mode="after")
    def _one(self):
        if (self.civicAddress is None) == (self.locationLabel is None):
            raise ValueError("CivicArea takes exactly one of civicAddress, locationLabel")
        return self


# TS 28.623 TimeWindow: `startTime` and `endTime` are RFC 3339 date-times (checked by pattern); either may be missing.
class TimeWindow(_S):
    startTime: str | None = None
    endTime: str | None = None

    @field_validator("startTime", "endTime")
    @classmethod
    def _date_time(cls, v):
        if v is not None and not _DATE_TIME.match(v):
            raise ValueError(f"{v!r} is not an RFC 3339 date-time")
        return v


# TS 28.623 TimeInterval: `intervalStart` and `intervalEnd` are RFC 3339 full-times (checked by pattern).
class TimeInterval(_S):
    intervalStart: str | None = None
    intervalEnd: str | None = None

    @field_validator("intervalStart", "intervalEnd")
    @classmethod
    def _full_time(cls, v):
        if v is not None and not _FULL_TIME.match(v):
            raise ValueError(f"{v!r} is not an RFC 3339 full-time")
        return v


DAYS = ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY")


# TS 28.623 SchedulingTime in one of four forms: a time window (`startTime` / `endTime`), `timeIntervals`, `daysOfWeek` or `daysOfMonth` (each of the
# last two with optional `timeIntervals`). `_form` enforces which combinations are allowed.
class SchedulingTime(BaseModel):
    """One of: a TimeWindow; `timeIntervals`; `daysOfWeek` (+ `timeIntervals`);
    `daysOfMonth` (+ `timeIntervals`)."""
    model_config = ConfigDict(extra="forbid")
    startTime: str | None = None
    endTime: str | None = None
    timeIntervals: list[TimeInterval] | None = None
    daysOfWeek: list[str] | None = Field(default=None, min_length=1, max_length=7)
    daysOfMonth: list[int] | None = None

    @model_validator(mode="after")
    def _form(self):
        """Checks that the object is one of the allowed forms; raises `ValueError` otherwise.

        Refused: an empty object; a window combined with intervals or days; `daysOfWeek` together with `daysOfMonth`; a repeated or unknown weekday; a repeated
        day of month or one outside 0..31. A window's times are checked by building a `TimeWindow` from them, which raises on a bad date-time.
        """
        window = self.startTime is not None or self.endTime is not None
        days_of_week, days_of_month = self.daysOfWeek, self.daysOfMonth
        weekly, monthly = days_of_week is not None, days_of_month is not None
        if not (window or self.timeIntervals is not None or weekly or monthly):
            raise ValueError("SchedulingTime is empty")
        if window and (self.timeIntervals is not None or weekly or monthly):
            raise ValueError("a TimeWindow (startTime / endTime) cannot be combined with intervals or days")
        if weekly and monthly:
            raise ValueError("daysOfWeek and daysOfMonth are alternatives")
        if window:
            TimeWindow(startTime=self.startTime, endTime=self.endTime)
        if days_of_week is not None and (any(d not in DAYS for d in days_of_week) or len(set(days_of_week)) != len(days_of_week)):
            raise ValueError(f"daysOfWeek are unique values of {', '.join(DAYS)}")
        if days_of_month is not None and (any(not 0 <= d <= 31 for d in days_of_month) or len(set(days_of_month)) != len(days_of_month)):
            raise ValueError("daysOfMonth are unique integers 0..31")
        return self


# TS 28.623 GeoCoordinate: `latitude` -90..90, `longitude` -180..180, optional `altitude`.
class GeoCoordinate(_S):
    altitude: float | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


# TS 28.623 GeoCircle: `distanceRadius` 1..65535 around a `referenceLocation`.
class GeoCircle(_S):
    distanceRadius: int | None = Field(default=None, ge=1, le=65535)
    referenceLocation: GeoCoordinate | None = None


# TS 28.623 GeoArea: exactly one of a `geoPolygon` (at least one coordinate; closure is not checked) or a `geoCircle`.
class GeoArea(_S):
    geoPolygon: list[GeoCoordinate] | None = Field(default=None, min_length=1)
    geoCircle: GeoCircle | None = None

    @model_validator(mode="after")
    def _one(self):
        if (self.geoPolygon is None) == (self.geoCircle is None):
            raise ValueError("GeoArea takes exactly one of geoPolygon, geoCircle")
        return self


# ---------------------------------------------------------------- ValueRangeType

# Structured alternatives of ValueRangeType, in the spec's order. A value must
# match at least one; the models reject unknown keys, so shapes do not blur.
# The ValueRangeType alternatives for an object value, in the spec's order. An object is accepted when any one validates (`value_range_problem`).
STRUCTURED = (("TimeWindow", TimeWindow), ("GeoArea", GeoArea), ("PlmnId", PlmnId), ("GeoCoordinate", GeoCoordinate),
              ("UEGroup", UEGroup), ("Frequency", Frequency), ("SchedulingTime", SchedulingTime), ("CivicArea", CivicArea))


def _validates(model: type[BaseModel], value: dict) -> str | None:
    """Returns None when `value` validates against `model`, else the pydantic errors flattened to one string (`field.path: message; ...`)."""
    try:
        model.model_validate(value)
    except ValidationError as exc:
        return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" if e["loc"] else e["msg"] for e in exc.errors())
    return None


def value_range_problem(value: Any) -> str | None:
    """Why `value` is not a ValueRangeType (or a list of them), or None."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return None
    if isinstance(value, list):
        for item in value:
            problem = value_range_problem(item)
            if problem:
                return problem
        return None
    if isinstance(value, dict):
        reasons = []
        for name, model in STRUCTURED:
            problem = _validates(model, value)
            if problem is None:
                return None
            reasons.append(f"{name}: {problem}")
        return "matches no ValueRangeType form (" + " | ".join(reasons) + ")"
    return f"{type(value).__name__} is not a ValueRangeType"


# datatype named by a specialised target / context (the families' own names)
# Specialised target / context names whose value must be a given datatype (the families' own names), used by `named_datatype_problem`.
NAMED_DATATYPES: dict[str, type[BaseModel]] = {"UEGroup": UEGroup, "CivicArea": CivicArea, "DlFrequency": Frequency,
                                                "UlFrequency": Frequency, "schedulingTime": SchedulingTime}


def named_datatype_problem(name: str, value: Any) -> str | None:
    """For a target / context named after one of the datatypes above, each
    value (a single object or a list of them) must be that datatype."""
    model = NAMED_DATATYPES.get(name)
    if model is None:
        return None
    for item in value if isinstance(value, list) else [value]:
        if not isinstance(item, dict):
            return f"{name} values are {model.__name__} objects"
        problem = _validates(model, item)
        if problem:
            return f"{model.__name__}: {problem}"
    return None


# ---------------------------------------------------------------- ReportingCondition

# TS 28.312 TargetFulfilmentCondition: a `targetName` with a Condition and a `targetValueRange` that must itself be a ValueRangeType.
# The condition list in `_condition` repeats the `Condition` literal of `ts28312.py`; keep the two equal.
class TargetFulfilmentCondition(_S):
    targetCondition: str
    targetName: str
    targetValueRange: Any

    @field_validator("targetCondition")
    @classmethod
    def _condition(cls, v):
        if v not in ("IS_EQUAL_TO", "IS_LESS_THAN", "IS_GREATER_THAN", "IS_WITHIN_RANGE", "IS_OUTSIDE_RANGE", "IS_ONE_OF",
                     "IS_NOT_ONE_OF", "IS_EQUAL_TO_OR_LESS_THAN", "IS_EQUAL_TO_OR_GREATER_THAN", "IS_ALL_OF"):
            raise ValueError(f"{v!r} is not a Condition")
        return v

    @field_validator("targetValueRange")
    @classmethod
    def _range(cls, v):
        problem = value_range_problem(v)
        if problem:
            raise ValueError(problem)
        return v


def reporting_condition_problem(value: Any) -> str | None:
    """A `ReportingCondition`: a TimeCondition (= SchedulingTime) or a TargetFulfilmentCondition."""
    if not isinstance(value, dict):
        return "a ReportingCondition is an object"
    time_problem = _validates(SchedulingTime, value)
    if time_problem is None:
        return None
    target_problem = _validates(TargetFulfilmentCondition, value)
    if target_problem is None:
        return None
    return f"neither a TimeCondition ({time_problem}) nor a TargetFulfilmentCondition ({target_problem})"
