"""SB-7: a VES event receiver (ONAP VES Event Listener 7.x, the way an O-RAN O1 adaptor reports events), mapped onto the paths RAN NF OAM already has.

What is here is the part with no database in it: who may post (HTTP Basic, SB-7.5), whether a posted event is well formed (SB-7.1), and what each domain of
event means for the platform (SB-7.2 to SB-7.4). `main.py` has the route and applies the result with the same helpers `POST /alarms/ingest`, `POST /pm-reports`
and `POST /o1-adaptor-endpoints/{id}/heartbeat` use, so a VES event and the same fact reported on those routes end up the same.

Accepted: the single-event form `{"event": {...}}` and the batch form `{"eventList": [{...}, ...]}`, at `/ves/eventListener/v7` and at
`/ves/eventListener/v7/eventBatch` (the path the listener spec gives the batch). An event is `commonEventHeader` plus one field set named by its `domain`.

| domain | needs | becomes |
|---|---|---|
| `fault` | `faultFields` | an alarm on the element `sourceName`: `eventSeverity` CRITICAL, MAJOR, MINOR or WARNING raises it (an open alarm with the same `alarmCondition` is updated, not repeated), NORMAL clears it |
| `heartbeat` | nothing more | a heartbeat of the O1 adaptor endpoint of the element `sourceName` |
| `measurement` | `measurementFields` | one PM report per entry of `additionalMeasurements` (`name` is the counter type; the numeric fields are the values; a field `cellId` names the cell, else `sourceName`) |
| `stndDefined`, namespace `3GPP-PerformanceAssurance` | `stndDefinedFields.data` | one PM report per `measInfoList` entry of the 3GPP `perf3gppFields` (the `sMeasInfoId` is the counter type, each `measObjInstId` a cell) |
| any other | | accepted and ignored, and the answer says so |

Not done, on purpose: the `eventDelay`/`eventThrottlingState` back-channel (the answer carries no commandList), `syslog`, `thresholdCrossingAlert`, `mobileFlow`, `sipSignaling`
and the other VES domains, and `stndDefined` fault supervision (3GPP-FaultSupervision). The listener has to be switched on by giving it a password; it is not R1-facing (an
adaptor posts to RAN NF OAM directly, as the stub does for its own emits): behind the gateway the bearer token is not a Basic credential, so the call is refused there.

What an answer says about an event is a fixed code, never the text of an error and never a value the sender wrote (a refused header names the member and the kind of error).
"""

import base64
import binascii
import datetime
import hmac
import logging
import os
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from smo_shared.errors import FrameworkError, framework_error, problem
from smo_shared.secretfile import SecretConflict, SecretFileError, read_secret

log = logging.getLogger("ran-nf-oam.ves")

REALM = "ran-nf-oam-ves"
USERNAME_VARIABLE = "RAN_NF_OAM_VES_USERNAME"
DEFAULT_USERNAME = "ves"
MAX_EVENTS = 500                                        # per post: a batch larger than this is refused (400), the sender splits it
MAX_NAME = 200

FAULT_SEVERITY = {"CRITICAL": "critical", "MAJOR": "major", "MINOR": "minor", "WARNING": "warning"}
PERF_NAMESPACE = "3GPP-PerformanceAssurance"


# ---------------------------------------------------------------- SB-7.5: who may post

def credentials() -> tuple[str, str] | None:
    """(username, password) of the listener, or None when it is not switched on (no password given). A password that is set twice (the variable and its
    `_FILE`) or a file that cannot be read stops the listener with 503 rather than leaving it open; the log says which, the answer does not."""
    try:
        password = read_secret("RAN_NF_OAM_VES_PASSWORD")     # or RAN_NF_OAM_VES_PASSWORD_FILE, the *_FILE helper
    except (SecretConflict, SecretFileError) as exc:
        log.error("the VES listener is off: %s", exc)
        raise framework_error(FrameworkError.ENDPOINT_UNREACHABLE, detail="the VES listener is not configured correctly") from exc
    if not password:
        return None
    return (os.environ.get(USERNAME_VARIABLE) or DEFAULT_USERNAME, password)


def basic_credentials(header: str | None) -> tuple[str, str] | None:
    """The user and password of an `Authorization: Basic ...` header; None for anything else (no header, another scheme, bad base64, no colon)."""
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "basic":
        return None
    try:
        decoded = base64.b64decode(value.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    user, colon, password = decoded.partition(":")
    return (user, password) if colon else None


def authenticate(header: str | None) -> None:
    """404 when the listener is not switched on (it does not exist for anyone), 401 with a Basic challenge when the credentials are missing or wrong.
    Both halves are compared whatever the first one said, in constant time."""
    expected = credentials()
    if expected is None:
        raise problem(404, "VES_LISTENER_DISABLED", "the VES event listener is not enabled on this RAN NF OAM")
    given = basic_credentials(header) or ("", "")
    user_ok = hmac.compare_digest(given[0].encode(), expected[0].encode())
    password_ok = hmac.compare_digest(given[1].encode(), expected[1].encode())
    if not (user_ok and password_ok and basic_credentials(header) is not None):
        error = problem(401, "VES_UNAUTHORIZED", "valid Basic credentials are required")
        error.headers = {"WWW-Authenticate": f'Basic realm="{REALM}", charset="UTF-8"'}
        raise error


# ---------------------------------------------------------------- SB-7.1: the schema

class CommonEventHeader(BaseModel):
    """The header every event has (VES 7.2 `commonEventHeader`; the required members of the spec are required here)."""
    model_config = ConfigDict(extra="allow")
    domain: str
    eventId: str
    eventName: str
    lastEpochMicrosec: float
    priority: Literal["High", "Medium", "Normal", "Low"]
    reportingEntityName: str
    sequence: int
    sourceName: str
    startEpochMicrosec: float
    version: str
    vesEventListenerVersion: str
    stndDefinedNamespace: str | None = None


class FaultFields(BaseModel):
    model_config = ConfigDict(extra="allow")
    faultFieldsVersion: str
    alarmCondition: str
    eventSeverity: Literal["CRITICAL", "MAJOR", "MINOR", "WARNING", "NORMAL"]
    eventSourceType: str
    specificProblem: str
    vfStatus: str
    alarmInterfaceA: str | None = None


class VesEnvelope(BaseModel):
    """The body of a post: one `event`, or an `eventList`."""
    model_config = ConfigDict(extra="allow")
    event: dict[str, Any] | None = None
    eventList: list[dict[str, Any]] | None = None


def events_of(envelope: VesEnvelope) -> list[dict[str, Any]]:
    """The events of a post, in order. 400 when it has neither form, both, none, or more than `MAX_EVENTS`."""
    if (envelope.event is None) == (envelope.eventList is None):
        raise problem(400, "VES_BAD_REQUEST", "send either event or eventList")
    events = [envelope.event] if envelope.event is not None else (envelope.eventList or [])
    if not events:
        raise problem(400, "VES_BAD_REQUEST", "eventList has no event")
    if len(events) > MAX_EVENTS:
        raise problem(400, "VES_BAD_REQUEST", f"at most {MAX_EVENTS} events per post")
    return events


def _where(error: ValidationError, index: int) -> str:
    """Which members of event `index` are wrong and how, without the values (a sender's value is not echoed)."""
    parts = []
    for item in error.errors(include_input=False, include_url=False)[:5]:
        parts.append(".".join(["commonEventHeader", *(str(p) for p in item["loc"])]) + ": " + item["type"])
    return f"event {index}: " + "; ".join(parts)


class ParsedEvent(BaseModel):
    index: int
    header: CommonEventHeader
    body: dict[str, Any]


def parse_events(events: list[dict[str, Any]]) -> list[ParsedEvent]:
    """Every event's header checked; 400 naming the first events that are wrong (member and error kind, no values) and nothing applied."""
    parsed, wrong = [], []
    for index, event in enumerate(events):
        try:
            parsed.append(ParsedEvent(index=index, header=CommonEventHeader.model_validate(event.get("commonEventHeader")), body=event))
        except ValidationError as exc:
            wrong.append(_where(exc, index))
    if wrong:
        raise problem(400, "VES_BAD_REQUEST", " | ".join(wrong[:5]) + (f" | and {len(wrong) - 5} more events" if len(wrong) > 5 else ""))
    return parsed


# ---------------------------------------------------------------- SB-7.2 to SB-7.4: what an event means

class AlarmAction(BaseModel):
    managed_element_ref: str
    source_alarm_id: str
    severity: str | None            # lower-case PerceivedSeverity; None: this event clears the alarm
    probable_cause: str
    specific_problem: str


class PmReport(BaseModel):
    managed_element_ref: str
    counter_type: str
    measurements: list[dict[str, Any]]      # {cellId, timestamp, values}, the shape of PmMeasurement


class HeartbeatAction(BaseModel):
    managed_element_ref: str


Action = AlarmAction | PmReport | HeartbeatAction


class Unsupported(Exception):
    """The event is valid but carries nothing this receiver maps; `reason` is the fixed code the answer gives."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _timestamp(header: CommonEventHeader) -> str:
    return datetime.datetime.fromtimestamp(header.lastEpochMicrosec / 1_000_000, tz=datetime.UTC).isoformat()


def _short(value: Any) -> str | None:
    return value if isinstance(value, str) and 0 < len(value) <= MAX_NAME else None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def fault_action(parsed: ParsedEvent) -> AlarmAction:
    try:
        fields = FaultFields.model_validate(parsed.body.get("faultFields"))
    except ValidationError as exc:
        raise Unsupported("FAULT_FIELDS_INVALID") from exc
    key = fields.alarmCondition + (f"/{fields.alarmInterfaceA}" if fields.alarmInterfaceA else "")
    if len(key) > MAX_NAME:
        raise Unsupported("FAULT_FIELDS_INVALID")
    return AlarmAction(managed_element_ref=parsed.header.sourceName, source_alarm_id=key, severity=FAULT_SEVERITY.get(fields.eventSeverity),
                       probable_cause=fields.alarmCondition, specific_problem=fields.specificProblem)


def measurement_reports(parsed: ParsedEvent) -> list[PmReport]:
    """One report per entry of `additionalMeasurements` that has a numeric field. A field is `{name, value}` in `arrayOfFields` (VES 7) or a pair of `hashMap`
    (VES 5 and 6); both are read."""
    fields = parsed.body.get("measurementFields")
    if not isinstance(fields, dict) or not isinstance(fields.get("additionalMeasurements"), list):
        raise Unsupported("MEASUREMENT_FIELDS_INVALID")
    when = _timestamp(parsed.header)
    element = parsed.header.sourceName
    reports = []
    for entry in fields["additionalMeasurements"]:
        if not isinstance(entry, dict) or _short(entry.get("name")) is None:
            continue
        pairs: dict[str, Any] = {}
        if isinstance(entry.get("arrayOfFields"), list):
            pairs.update({f["name"]: f.get("value") for f in entry["arrayOfFields"] if isinstance(f, dict) and isinstance(f.get("name"), str)})
        if isinstance(entry.get("hashMap"), dict):
            pairs.update(entry["hashMap"])
        values = {name: number for name, raw in pairs.items() if name != "cellId" and (number := _number(raw)) is not None}
        if values:
            cell = _short(pairs.get("cellId")) or element
            reports.append(PmReport(managed_element_ref=element, counter_type=entry["name"],
                                    measurements=[{"cellId": cell, "timestamp": when, "values": values}]))
    if not reports:
        raise Unsupported("NO_NUMERIC_MEASUREMENT")
    return reports


def perf3gpp_reports(parsed: ParsedEvent) -> list[PmReport]:
    """The 3GPP `perf3gppFields` (TS 28.532 performance assurance as VES carries it): `measDataCollection.measInfoList[]`, each with the names of its counters
    (`measTypes.sMeasTypesList`) and, per object instance, the results by position (`measResults[{p, sValue}]`). A value that is suspect (`suspectFlag` true)
    or not a number is left out."""
    data = (parsed.body.get("stndDefinedFields") or {}).get("data") if isinstance(parsed.body.get("stndDefinedFields"), dict) else None
    if isinstance(data, dict) and isinstance(data.get("event"), dict):
        data = data["event"]
    perf = data.get("perf3gppFields") if isinstance(data, dict) else None
    info_list = ((perf or {}).get("measDataCollection") or {}).get("measInfoList") if isinstance(perf, dict) else None
    if not isinstance(info_list, list):
        raise Unsupported("PERF3GPP_FIELDS_INVALID")
    when = _timestamp(parsed.header)
    element = parsed.header.sourceName
    reports = []
    for info in info_list:
        if not isinstance(info, dict):
            continue
        counter = _short((info.get("measInfoId") or {}).get("sMeasInfoId")) if isinstance(info.get("measInfoId"), dict) else None
        types = (info.get("measTypes") or {}).get("sMeasTypesList") if isinstance(info.get("measTypes"), dict) else None
        if counter is None or not isinstance(types, list) or not isinstance(info.get("measValuesList"), list):
            continue
        measurements = []
        for item in info["measValuesList"]:
            if not isinstance(item, dict) or _short(item.get("measObjInstId")) is None or str(item.get("suspectFlag", "false")).lower() == "true":
                continue
            values = {}
            results = item.get("measResults")
            for result in results if isinstance(results, list) else []:
                place = result.get("p") if isinstance(result, dict) else None
                number = _number(result.get("sValue")) if isinstance(result, dict) else None
                if isinstance(place, int) and not isinstance(place, bool) and 1 <= place <= len(types) and isinstance(types[place - 1], str) and number is not None:
                    values[types[place - 1]] = number
            if values:
                measurements.append({"cellId": item["measObjInstId"], "timestamp": when, "values": values})
        if measurements:
            reports.append(PmReport(managed_element_ref=element, counter_type=counter, measurements=measurements))
    if not reports:
        raise Unsupported("NO_NUMERIC_MEASUREMENT")
    return reports


def actions_for(parsed: ParsedEvent) -> list[Action]:
    """What the platform does for one event, or `Unsupported` with the reason."""
    domain = parsed.header.domain
    if domain == "fault":
        return [fault_action(parsed)]
    if domain == "heartbeat":
        return [HeartbeatAction(managed_element_ref=parsed.header.sourceName)]
    if domain == "measurement":
        return list(measurement_reports(parsed))
    if domain == "stndDefined" and parsed.header.stndDefinedNamespace == PERF_NAMESPACE:
        return list(perf3gpp_reports(parsed))
    raise Unsupported("DOMAIN_NOT_MAPPED")
