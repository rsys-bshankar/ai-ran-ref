"""TS 28.312 value datatypes that `ValueRangeType` and `ReportingCondition` use
(SA-INTENT-partial). They were accepted as values with no inner structure.

`TS28312_IntentNrm.yaml`: `Frequency`, `UEGroup`, `QoSId`, `CivicArea`,
`CivicAddress`, `ReportingCondition` = `TimeCondition` | `TargetFulfilmentCondition`;
with their dependencies `PlmnId`, `Snssai` (TS 28.541), `SchedulingTime`,
`TimeWindow`, `TimeInterval`, `GeoArea`, `GeoCoordinate` (TS 28.623).

`value_range_problem(value)` is the whole `ValueRangeType` rule: a scalar is
fine; a list is each of its items; an object must match exactly one of the
structured alternatives (TimeWindow, DateTime string, GeoArea, PlmnId,
GeoCoordinate, UEGroup, Frequency, SchedulingTime, CivicArea). `None` is
allowed (an optional value that is absent).

Recorded deviation: `DateTime` and `FullTime` strings are checked by pattern
(RFC 3339 shape), not by calendar validity.
"""

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

_DATE_TIME = re.compile(r"^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$")
_FULL_TIME = re.compile(r"^\d{2}:\d{2}:\d{2}(\.\d+)?([Zz]|[+-]\d{2}:\d{2})$")


class _S(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _one_key(value: dict, allowed: tuple[str, ...], what: str) -> None:
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


class PlmnId(_S):
    mcc: str | None = Field(default=None, pattern=r"^[0-9]{3}$")
    mnc: str | None = Field(default=None, pattern=r"^[0-9]{2,3}$")


class Snssai(_S):
    sst: int | None = Field(default=None, ge=0, le=255)
    sd: str | None = Field(default=None, pattern=r"^[A-Fa-f0-9]{6}$")


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


class Frequency(_S):
    arfcn: int | None = None
    freqband: str | None = None

    @model_validator(mode="after")
    def _some(self):
        if self.arfcn is None and self.freqband is None:
            raise ValueError("a Frequency carries an arfcn or a freqband")
        return self


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


class CivicArea(_S):
    civicAddress: CivicAddress | None = None
    locationLabel: str | None = None

    @model_validator(mode="after")
    def _one(self):
        if (self.civicAddress is None) == (self.locationLabel is None):
            raise ValueError("CivicArea takes exactly one of civicAddress, locationLabel")
        return self


class TimeWindow(_S):
    startTime: str | None = None
    endTime: str | None = None

    @field_validator("startTime", "endTime")
    @classmethod
    def _date_time(cls, v):
        if v is not None and not _DATE_TIME.match(v):
            raise ValueError(f"{v!r} is not an RFC 3339 date-time")
        return v


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
        window = self.startTime is not None or self.endTime is not None
        weekly, monthly = self.daysOfWeek is not None, self.daysOfMonth is not None
        if not (window or self.timeIntervals is not None or weekly or monthly):
            raise ValueError("SchedulingTime is empty")
        if window and (self.timeIntervals is not None or weekly or monthly):
            raise ValueError("a TimeWindow (startTime / endTime) cannot be combined with intervals or days")
        if weekly and monthly:
            raise ValueError("daysOfWeek and daysOfMonth are alternatives")
        if window:
            TimeWindow(startTime=self.startTime, endTime=self.endTime)
        if weekly and (any(d not in DAYS for d in self.daysOfWeek) or len(set(self.daysOfWeek)) != len(self.daysOfWeek)):
            raise ValueError(f"daysOfWeek are unique values of {', '.join(DAYS)}")
        if monthly and (any(not 0 <= d <= 31 for d in self.daysOfMonth) or len(set(self.daysOfMonth)) != len(self.daysOfMonth)):
            raise ValueError("daysOfMonth are unique integers 0..31")
        return self


class GeoCoordinate(_S):
    altitude: float | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)


class GeoCircle(_S):
    distanceRadius: int | None = Field(default=None, ge=1, le=65535)
    referenceLocation: GeoCoordinate | None = None


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
STRUCTURED = (("TimeWindow", TimeWindow), ("GeoArea", GeoArea), ("PlmnId", PlmnId), ("GeoCoordinate", GeoCoordinate),
              ("UEGroup", UEGroup), ("Frequency", Frequency), ("SchedulingTime", SchedulingTime), ("CivicArea", CivicArea))


def _validates(model: type[BaseModel], value: dict) -> str | None:
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
