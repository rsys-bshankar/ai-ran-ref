"""PR-GUI-9.8: the managed elements as a fleet: the site cluster of an element, the health map by region or site cluster, and the worst elements.

The routes of this router read the registry (`managed_entity`) and the open alarms (`alarm`) and answer aggregates computed in SQL, one query each,
so the console shows a health map and a ranking without reading every alarm. `main.py` includes this router BEFORE the vendors router (`vendors.py`),
whose `GET /managed-entities/{managed_element_ref}` would otherwise take `health` and `worst` for element references.

Definitions (also in the module README, section 2.4):
  - an element is **unhealthy** when it has at least one open (not cleared) alarm of severity critical or major;
  - **healthScore** = 100 x (elements - unhealthy) / elements over the elements the answer covers, rounded to one decimal; null when there are none;
  - **worstSeverity** of a group is the worst graded severity (critical, major, minor, warning) of an open alarm on any of its elements; null when none.

Every answer is limited to the caller's scope claim (ADR 0005, `scoping.py`): a scoped caller's map and ranking hold only the elements it may see.
The site cluster is a grouping only: it is not part of the scope rule. The one write here, `PUT /managed-entities/{me}/site-cluster`, is an admin's
call in the GUI backend's rules.
"""

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from smo_shared import scope as authz_scope
from smo_shared.db import get_session
from smo_shared.errors import FrameworkError, framework_error

from . import alarm_query, scoping
from .models import Alarm, ManagedEntity

router = APIRouter()

UNHEALTHY_RANK = alarm_query.SEVERITY_RANK["major"]           # critical (0) or major (1): an open alarm at or above this rank makes an element unhealthy
WORST_RANK = alarm_query.SEVERITY_RANK["warning"]             # the graded severities; indeterminate says nothing about how bad, so it is not a "worst"
RANK_NAMES = {rank: name for name, rank in alarm_query.SEVERITY_RANK.items()}


class SiteClusterBody(BaseModel):
    """The site cluster of an element (`null` clears it). Same alphabet as a region: 1 to 100 letters, digits and `. _ : / @ + -`."""
    model_config = ConfigDict(extra="forbid")
    siteCluster: str | None = Field(..., max_length=100)

    @field_validator("siteCluster")
    @classmethod
    def _valid(cls, value: str | None) -> str | None:
        """Refuse a value outside the alphabet of a scope value (a cluster name ends up in URLs and filters)."""
        if value is not None and not authz_scope.valid_value(value):
            raise ValueError("must be 1 to 100 characters of letters, digits and . _ : / @ + -, starting with a letter or digit")
        return value


@router.put("/managed-entities/{managed_element_ref}/site-cluster")
def set_site_cluster(managed_element_ref: str, body: SiteClusterBody, db: Session = Depends(get_session)):
    """PR-GUI-9.8: set (or with `null` clear) the site cluster of a managed element → `{managedElementRef, siteCluster}`. Commits. 404
    `MANAGED_ENTITY_NOT_FOUND` for an unknown element; 422 for a value outside the alphabet. An admin's call in the GUI backend."""
    me = db.get(ManagedEntity, managed_element_ref)
    if me is None:
        raise framework_error(FrameworkError.MANAGED_ENTITY_NOT_FOUND, detail=f"no managed entity {managed_element_ref!r}")
    me.site_cluster = body.siteCluster
    db.commit()
    return {"managedElementRef": me.managed_element_ref, "siteCluster": me.site_cluster}


def _element_alarm_summary(request: Request, region: str | None):
    """A subquery with one row per managed element the caller may see (narrowed to `region` when given): its `region`, `site_cluster`, the counts of its
    open `critical`, `major` and all open alarms, and `worst_rank`, the best (lowest) rank among its open graded alarms (NULL when it has none)."""
    rank = alarm_query.severity_rank()
    open_alarms = select(Alarm.managed_element_ref.label("ref"),
                         func.sum(case((rank == 0, 1), else_=0)).label("critical"),
                         func.sum(case((rank == 1, 1), else_=0)).label("major"),
                         func.count().label("open_alarms"),
                         func.min(case((rank <= WORST_RANK, rank))).label("worst_rank")) \
        .where(Alarm.severity != "cleared").group_by(Alarm.managed_element_ref).subquery()
    stmt = select(ManagedEntity.managed_element_ref, ManagedEntity.region, ManagedEntity.site_cluster,
                  func.coalesce(open_alarms.c.critical, 0).label("critical"), func.coalesce(open_alarms.c.major, 0).label("major"),
                  func.coalesce(open_alarms.c.open_alarms, 0).label("open_alarms"), open_alarms.c.worst_rank) \
        .outerjoin(open_alarms, open_alarms.c.ref == ManagedEntity.managed_element_ref)
    stmt = scoping.scoped_to_elements(stmt, scoping.request_scope(request), ManagedEntity.managed_element_ref)
    if region:
        stmt = stmt.where(ManagedEntity.region == region)
    return stmt.subquery()


@router.get("/managed-entities/health")
def fleet_health(request: Request, group_by: Literal["region", "site_cluster"] = "region", region: str | None = None,
                 db: Session = Depends(get_session)):
    """PR-GUI-9.8: the health map. `{"groups": [{"key", "elements", "unhealthy", "worstSeverity"}], "healthScore"}`, one group per `group_by` value
    (`region` or `site_cluster`; elements without one are the group `null`), ordered by unhealthy count then key. Unhealthy: an element with an open
    critical or major alarm. worstSeverity: the worst graded severity of an open alarm in the group, or null. healthScore: 100 x (elements - unhealthy)
    / elements over all the groups, one decimal, null when there are no elements. `region` narrows to one region; the caller's scope claim applies."""
    elements = _element_alarm_summary(request, region)
    key = elements.c[group_by]
    unhealthy = func.sum(case((elements.c.worst_rank <= UNHEALTHY_RANK, 1), else_=0))           # NULL worst_rank (no graded open alarm) counts 0
    rows = db.execute(select(key.label("key"), func.count().label("elements"), unhealthy.label("unhealthy"), func.min(elements.c.worst_rank).label("worst"))
                      .group_by(key).order_by(unhealthy.desc(), key)).all()
    groups = [{"key": row.key, "elements": row.elements, "unhealthy": int(row.unhealthy or 0),
               "worstSeverity": RANK_NAMES.get(row.worst) if row.worst is not None else None} for row in rows]
    total, sick = sum(g["elements"] for g in groups), sum(g["unhealthy"] for g in groups)
    return {"groupBy": group_by, "groups": groups, "healthScore": round(100 * (total - sick) / total, 1) if total else None}


@router.get("/managed-entities/worst")
def worst_elements(request: Request, limit: int = Query(10, ge=1, le=100), region: str | None = None, db: Session = Depends(get_session)):
    """PR-GUI-9.8: the elements with open alarms, worst first: `[{managedElementRef, region, siteCluster, critical, major, openAlarms}]` ranked by open
    critical alarms, then open major, then all open alarms (then the reference), in one SQL query. An element with no open alarm is not listed.
    `region` narrows to one region; the caller's scope claim applies."""
    elements = _element_alarm_summary(request, region)
    rows = db.execute(select(elements).where(elements.c.open_alarms > 0)
                      .order_by(elements.c.critical.desc(), elements.c.major.desc(), elements.c.open_alarms.desc(), elements.c.managed_element_ref)
                      .limit(limit)).all()
    return [{"managedElementRef": row.managed_element_ref, "region": row.region, "siteCluster": row.site_cluster,
             "critical": int(row.critical), "major": int(row.major), "openAlarms": int(row.open_alarms)} for row in rows]
