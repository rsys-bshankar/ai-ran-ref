"""Computing a KPI from stored PM data (PR-MGT-11.3, 11.4).

The samples are the measurements in the PM files RAN NF OAM has stored (`pm_file`): each has a cell, a timestamp and a value (the counter named by the
file's `counterType`) or several (`values`, counter name to value). A KPI's counters are combined per counter over the period (sum, avg, min, max, last,
count) and over the cells of the group asked for, and the formula is evaluated over the results. A ratio is therefore correct at every level: a region's
success rate is the region's successes over the region's attempts, not the average of its cells' rates.

Only stored files are read: a report that went straight to DME (`/pm-reports`) leaves nothing here. A bounded number of the newest files is read
(`RAN_NF_OAM_KPI_MAX_FILES`); when there are more the answer says `truncated`. PM collection at scale (MGT-12) is where this stops being a scan.
"""

import datetime
import json
import os
import re
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import kpi_formula
from smo_shared.scope import Scope

from .models import KpiDefinition, ManagedEntity, PMFile
from .scoping import scoped_to_elements

AGGREGATIONS = ("sum", "avg", "min", "max", "last", "count")
GROUPS = ("cell", "element", "sectorGroup", "incidentZone", "all")
MAX_FILES = int(os.environ.get("RAN_NF_OAM_KPI_MAX_FILES", "2000"))
MAX_GROUPS = 1000
NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
VARIABLE = re.compile(r"[^A-Za-z0-9_]")


def normalise_counters(formula: str, counters: list[dict] | None) -> list[dict]:
    """The counter table of a definition, checked: every variable of the formula is fed by exactly one counter, aggregations are known, nothing is
    declared twice or unused. With no table given, each variable of the formula is a counter of the same name, summed."""
    wanted = kpi_formula.names(formula)
    if not counters:
        return [{"counter": v, "variable": v, "aggregation": "sum"} for v in wanted]
    table, seen = [], set()
    for entry in counters:
        counter = str(entry.get("counter", ""))
        variable = str(entry.get("variable") or VARIABLE.sub("_", counter))
        aggregation = entry.get("aggregation", "sum")
        if not counter or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", variable):
            raise kpi_formula.FormulaError(f"counter {counter!r} needs a name and a variable that is an identifier")
        if aggregation not in AGGREGATIONS:
            raise kpi_formula.FormulaError(f"aggregation {aggregation!r} is not one of {', '.join(AGGREGATIONS)}")
        if variable in seen:
            raise kpi_formula.FormulaError(f"variable {variable} is declared twice")
        seen.add(variable)
        table.append({"counter": counter, "variable": variable, "aggregation": aggregation})
    missing = [v for v in wanted if v not in seen]
    if missing:
        raise kpi_formula.FormulaError(f"the formula uses {', '.join(missing)}, which no counter feeds")
    unused = sorted(seen - set(wanted))
    if unused:
        raise kpi_formula.FormulaError(f"{', '.join(unused)} is declared but not used in the formula")
    return table


@dataclass
class Sample:
    """One measurement of one counter for one cell at one time, read from a stored PM file."""
    element: str
    cell: str
    counter: str
    at: datetime.datetime
    value: float


def _as_utc(value: datetime.datetime) -> datetime.datetime:
    return value if value.tzinfo else value.replace(tzinfo=datetime.UTC)


def load_samples(db: Session, counters: set[str], start: datetime.datetime, end: datetime.datetime,
                 element: str | None = None, cell: str | None = None, scope: Scope | None = None,
                 exclude: list[str] | None = None) -> tuple[list[Sample], int, bool]:
    """(samples in [start, end) of the counters asked for, files read, whether more files than the bound were left unread). `scope` (PR-SEC-10.6) limits the files
    to those of the elements a caller with that claim may touch: a KPI over "all" is then over its elements, not the network's. `exclude` (MGT-2.6) leaves out the
    files of those elements (the ones a caller's access rules do not let it read)."""
    stmt = scoped_to_elements(select(PMFile), scope, PMFile.managed_element_ref).order_by(PMFile.file_ready_time.desc())
    if exclude:
        stmt = stmt.where(PMFile.managed_element_ref.not_in(exclude))
    if element:
        stmt = stmt.where(PMFile.managed_element_ref == element)
    files = db.scalars(stmt.limit(MAX_FILES + 1)).all()
    truncated = len(files) > MAX_FILES
    samples: list[Sample] = []
    for pm_file in files[:MAX_FILES]:
        try:
            measurements = json.loads(pm_file.content).get("measurements", [])
        except (ValueError, AttributeError):
            continue
        for m in measurements:
            if not isinstance(m, dict) or (cell and m.get("cellId") != cell):
                continue
            try:
                at = _as_utc(datetime.datetime.fromisoformat(str(m["timestamp"]).replace("Z", "+00:00")))
            except (KeyError, ValueError):
                continue
            if not start <= at < end:
                continue
            values = dict(m.get("values") or {})
            if m.get("value") is not None:
                values.setdefault(pm_file.counter_type, m["value"])
            for counter, value in values.items():
                if counter in counters and isinstance(value, (int, float)) and not isinstance(value, bool):
                    samples.append(Sample(pm_file.managed_element_ref, str(m.get("cellId")), counter, at, float(value)))
    return samples, min(len(files), MAX_FILES), truncated


def _combine(aggregation: str, samples: list[Sample]) -> float | None:
    """The aggregation (sum, avg, min, max, count, or last by timestamp) of one counter's samples in a group; None when the group has no sample of it.
    """
    if not samples:
        return None
    values = [s.value for s in samples]
    if aggregation == "sum":
        return sum(values)
    if aggregation == "avg":
        return sum(values) / len(values)
    if aggregation == "min":
        return min(values)
    if aggregation == "max":
        return max(values)
    if aggregation == "count":
        return float(len(values))
    return max(samples, key=lambda s: s.at).value                      # last


def _group_key(db: Session, group_by: str, sample: Sample, guards: dict) -> tuple:
    """The key of the group a sample belongs to for `group_by`: (element, cell), (element,), () for all, or the cell's guard attribute (`sectorGroup`, `incidentZone`; 'unassigned' when the cell has none). `guards` caches each element's cell guards across the samples of one computation so the registry is read once per element.
    """
    if group_by == "cell":
        return (sample.element, sample.cell)
    if group_by == "element":
        return (sample.element,)
    if group_by == "all":
        return ()
    attribute = group_by                                                # sectorGroup / incidentZone: the cell guard attribute of the cell
    if sample.element not in guards:
        row = db.get(ManagedEntity, sample.element)
        guards[sample.element] = (row.cell_guards if row else None) or {}
    return (str((guards[sample.element].get(sample.cell) or {}).get(attribute) or "unassigned"),)


def _group_view(group_by: str, key: tuple) -> dict:
    """The `group` object of a result item for a group key: the element and cell refs, the element ref, `{}` for all, or `{group_by: value}` for a guard attribute.
    """
    if group_by == "cell":
        return {"managedElementRef": key[0], "cellId": key[1]}
    if group_by == "element":
        return {"managedElementRef": key[0]}
    if group_by == "all":
        return {}
    return {group_by: key[0]}


def compute(db: Session, definition: KpiDefinition, start: datetime.datetime, end: datetime.datetime, group_by: str = "cell",
            element: str | None = None, cell: str | None = None, scope: Scope | None = None, exclude: list[str] | None = None) -> dict:
    """The KPI over [start, end), one item per group. A group with no samples of a needed counter, or whose formula is undefined (a division by
    zero), has `value` null and says which (`NO_DATA`, `UNDEFINED`)."""
    table = definition.counters
    samples, scanned, truncated = load_samples(db, {c["counter"] for c in table}, start, end, element, cell, scope, exclude)
    grouped: dict[tuple, list[Sample]] = {}
    guards: dict = {}
    for sample in samples:
        grouped.setdefault(_group_key(db, group_by, sample, guards), []).append(sample)
    items = []
    for key in sorted(grouped)[:MAX_GROUPS]:
        group_samples = grouped[key]
        counters = {c["variable"]: _combine(c["aggregation"], [s for s in group_samples if s.counter == c["counter"]]) for c in table}
        value = kpi_formula.evaluate(definition.formula, counters)
        reason = None if value is not None else ("NO_DATA" if any(v is None for v in counters.values()) else "UNDEFINED")
        observations = len({(smp.element, smp.cell, smp.at) for smp in group_samples})        # a measurement with several counters is one observation
        items.append({"group": _group_view(group_by, key), "value": value, "samples": observations, "counters": counters, "reason": reason})
    if group_by == "all" and not items:                                # one question, one answer: "no data" rather than an empty list
        items.append({"group": {}, "value": None, "samples": 0, "counters": {c["variable"]: None for c in table}, "reason": "NO_DATA"})
    return {"kpi": definition.name, "unit": definition.unit, "from": start.isoformat(), "to": end.isoformat(), "groupBy": group_by,
            "filesScanned": scanned, "truncated": truncated or len(grouped) > MAX_GROUPS, "items": items}


# MGT-11.6: the KPIs this service seeds. They are defined over the PM counters this build carries (the names its sample rApps read and its mock NF
# reports, TS 28.552 style), with the usual shape of the KPI: a ratio is the group's summed counters divided, a level is the mean of its samples.
# They are NOT the TS 28.554 definitions, which are not reproduced here: an operator who needs those defines them (`PUT /kpi-definitions/{name}`)
# over the counters their NFs report.
HO_FAILURES = "(fail_too_late + fail_too_early + fail_wrong_cell)"
STANDARD_KPIS: list[dict[str, Any]] = [
    {"name": "dl_prb_utilization", "unit": "percent", "formula": "prb",
     "description": "Mean downlink PRB utilisation (RRU.PrbTotDl is reported as a percentage per sample)",
     "counters": [{"counter": "RRU.PrbTotDl", "variable": "prb", "aggregation": "avg"}]},
    {"name": "rrc_connected_ues_mean", "unit": "ues", "formula": "ues",
     "description": "Mean number of RRC-connected UEs",
     "counters": [{"counter": "RRC.ConnMean", "variable": "ues", "aggregation": "avg"}]},
    {"name": "dl_ue_throughput", "unit": "Mbit/s", "formula": "thp",
     "description": "Mean downlink UE throughput",
     "counters": [{"counter": "DRB.UEThpDl", "variable": "thp", "aggregation": "avg"}]},
    {"name": "handover_failure_rate", "unit": "percent", "formula": f"100 * {HO_FAILURES} / att",
     "description": "Handovers that failed too late, too early or to the wrong cell, of the handovers attempted",
     "counters": [{"counter": "MM.HoExeAtt", "variable": "att", "aggregation": "sum"},
                  {"counter": "MM.HoFailTooLate", "variable": "fail_too_late", "aggregation": "sum"},
                  {"counter": "MM.HoFailTooEarly", "variable": "fail_too_early", "aggregation": "sum"},
                  {"counter": "MM.HoFailWrongCell", "variable": "fail_wrong_cell", "aggregation": "sum"}]},
    {"name": "handover_success_rate", "unit": "percent", "formula": f"100 * (att - {HO_FAILURES}) / att",
     "description": "Handovers that did not fail too late, too early or to the wrong cell, of the handovers attempted",
     "counters": [{"counter": "MM.HoExeAtt", "variable": "att", "aggregation": "sum"},
                  {"counter": "MM.HoFailTooLate", "variable": "fail_too_late", "aggregation": "sum"},
                  {"counter": "MM.HoFailTooEarly", "variable": "fail_too_early", "aggregation": "sum"},
                  {"counter": "MM.HoFailWrongCell", "variable": "fail_wrong_cell", "aggregation": "sum"}]},
    {"name": "handover_ping_pong_rate", "unit": "percent", "formula": "100 * ping_pong / att",
     "description": "Handovers that bounced straight back, of the handovers attempted",
     "counters": [{"counter": "MM.HoExeAtt", "variable": "att", "aggregation": "sum"},
                  {"counter": "MM.HoPingPong", "variable": "ping_pong", "aggregation": "sum"}]},
]


def seed_standard_kpis(db: Session) -> tuple[list[str], list[str]]:
    """(created, kept): inserts each standard KPI that is not defined yet and leaves one that is (an operator's own edit of it stays). Idempotent."""
    created, kept = [], []
    for spec in STANDARD_KPIS:
        if db.get(KpiDefinition, spec["name"]) is not None:
            kept.append(spec["name"])
            continue
        table = normalise_counters(spec["formula"], spec["counters"])              # the seed is held to the same rules as any definition
        db.add(KpiDefinition(name=spec["name"], formula=spec["formula"], counters=table, unit=spec["unit"], description=spec["description"]))
        created.append(spec["name"])
    return created, kept

