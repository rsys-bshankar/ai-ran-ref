"""Summary counts for the console's tiles, badges and meters (the GUI redesign, SCALE.md P2): `GET /api/summary/{page}`.

Before this, every tile counted rows of the first page of a list in the browser, so past 100 rows (the SMO's default page size,
smo_shared/pagination.py) every count was wrong. Here the BFF asks each module for a one-row page with the filter that defines the count
(`?severity=critical&limit=1`) and reads the envelope's `total`, which the module computes with `COUNT(*)` in SQL. The calls of one page run in
parallel, and the answer is kept `CACHE_SECONDS` and shared by every signed-in user: the counts are module-wide reads that every role may make
(app/rbac.py lets a viewer read every module), so no user's view differs from another's.

Installed by app/main.py. Reads only: nothing here writes. A count whose module did not answer is `null` and named in `partial`, so a tile shows
"—" and the box says which module is missing (SCALE.md P10) instead of the whole summary failing. Which counts a page gets is the `PAGES` table
below; the GUI's `src/data/summary.ts` reads the same keys. A few values are not a `total` but one field of a module's statistics answer (the mean
time to acknowledge, `alarms.mtta`, from RAN NF OAM's `/alarms/stats`; the number of stopped rApps from rApp Management's `GET /kill-all`): a
`Count` with `field` set. Such a field may be null in a good answer (no alarm acknowledged in the window), which is not a missing module.
A count that needs data no module serves today (a network health score) is deliberately not here: the GUI shows "—" for it (BRIEF §5).

The same cached computation feeds the event stream (app/events.py): `install` keeps it on `app.state.summary_page`, so a page pushed to fifty
open streams and asked by fifty tiles is still one fan-out per `CACHE_SECONDS`.
"""

import asyncio
import datetime
import logging
import time
from dataclasses import dataclass, field

import httpx
from fastapi import Depends, FastAPI

from .rbac import decide
from .smo_client import SmoAuthError

log = logging.getLogger("smo-gui-bff")

CACHE_SECONDS = 5.0
COUNT_TIMEOUT_SECONDS = 5.0


@dataclass(frozen=True)
class Count:
    """One count: the `total` of `path` filtered by `params`. `since_hours` adds a `since` parameter that many hours before now (decisions in 24 h).
    With `field`, the value is that top-level field of the answer (a number or null) instead of `total`, and no one-row page is asked for."""
    path: str
    params: tuple[tuple[str, str], ...] = ()
    since_hours: int | None = None
    field: str | None = None


def _states(path: str, param: str, states: tuple[str, ...], prefix: str) -> dict[str, Count]:
    """`{prefix.STATE: Count}` for each state, plus `prefix.total` unfiltered."""
    out = {f"{prefix}.{s}": Count(path, ((param, s),)) for s in states}
    out[f"{prefix}.total"] = Count(path)
    return out


SEVERITIES = ("critical", "major", "minor", "warning", "cleared")
ALARMS = {**{f"alarms.{s}": Count("/ran-nf-oam/alarms", (("severity", s),)) for s in SEVERITIES},
          "alarms.total": Count("/ran-nf-oam/alarms"), "ocloudAlarms.total": Count("/focom/alarms"),
          # GUI-9.8: the alarms nobody acknowledged yet, and the mean time to acknowledge (seconds, over the alarms acknowledged in the last 24 h)
          "alarms.unacked": Count("/ran-nf-oam/alarms", (("ack_state", "UNACKNOWLEDGED"),)),
          "alarms.mtta": Count("/ran-nf-oam/alarms/stats", (("window_hours", "24"),), field="mttaSeconds")}
APPROVALS = {"approvals.PENDING": Count("/ran-nf-oam/rapp-approvals", (("status", "PENDING"),))}
INSTANCES = _states("/rapp-mgmt/instances", "state", ("DEPLOYING", "RUNNING", "UPGRADING", "UNDEPLOYED", "FAULTED"), "instances")
STOPPED = {"rappsStopped": Count("/rapp-mgmt/kill-all", field="stopped")}     # GUI-9.6: how many rApps have their writes stopped
PACKAGES = _states("/onboarding/packages", "state", ("ONBOARDING", "AVAILABLE", "PRIMED", "DEPRECATED", "FAILED"), "packages")
DEPLOYMENTS = _states("/nfo/deployments", "state", ("INSTANTIATING", "RUNNING", "UPDATING", "TERMINATING", "ABNORMAL"), "deployments")
INTENTS = _states("/intent-service/intents", "admin_state", ("ACTIVATED", "DEACTIVATED"), "intents")
CONFIG_JOBS = _states("/ran-nf-oam/config-jobs", "status", ("PENDING", "PROCESSING", "HALTED", "COMPLETED", "PARTIAL_SUCCESS", "FAILED"), "configJobs")
CAMPAIGNS = _states("/ran-nf-oam/software-campaigns", "status",
                    ("PENDING", "RUNNING", "HALTED", "COMPLETED", "ABORTED", "ROLLING_BACK", "ROLLED_BACK", "ROLLBACK_FAILED"), "campaigns")
DECISIONS_24H = {f"decisions24h.{d}": Count("/ran-nf-oam/decision-records", (("disposition", d),), since_hours=24)
                 for d in ("DIRECT", "APPROVED", "REJECTED", "EXPIRED", "REFUSED")}
DECISIONS_24H["decisions24h.total"] = Count("/ran-nf-oam/decision-records", since_hours=24)
ESCALATIONS = {"escalations.total": Count("/sa-smos/remedial-actions", (("outcome", "ESCALATED"),))}
BREACHES = {"mlmfBreaches.total": Count("/aimgf/mlmf/reports", (("breached_only", "true"),))}
MODELS = {"models.total": Count("/mlmr/models"), "trainingJobs.total": Count("/aimgf/training-jobs")}
ELEMENTS = {"elements.total": Count("/ran-nf-oam/managed-entities")}

# The counts each page asks for, by the page's name in the URL. "nav" is the sidebar's badges, asked on every page, so it is kept small.
PAGES: dict[str, dict[str, Count]] = {
    "nav": {"alarms.critical": ALARMS["alarms.critical"], "alarms.major": ALARMS["alarms.major"], **APPROVALS,
            "configJobs.HALTED": CONFIG_JOBS["configJobs.HALTED"], "campaigns.HALTED": CAMPAIGNS["campaigns.HALTED"]},
    "dashboard": {**ALARMS, **APPROVALS, **INSTANCES, **PACKAGES, **DEPLOYMENTS, **INTENTS, **DECISIONS_24H, **ESCALATIONS, **BREACHES, **MODELS,
                  **ELEMENTS, **STOPPED},
    "alarms": ALARMS,
    "rapps": {**INSTANCES, **PACKAGES, **STOPPED},
    "approvals": APPROVALS,
    "decisions": DECISIONS_24H,
    "infrastructure": {**DEPLOYMENTS, **ELEMENTS},
    "configuration": CONFIG_JOBS,
    "software": CAMPAIGNS,
    "intents": INTENTS,
    "aiml": {**MODELS, **BREACHES},
}


@dataclass
class _Cached:
    at: float
    body: dict = field(default_factory=dict)


def page_allowed(page: str, role) -> bool:
    """Whether `role` may read every count of `page` (each is a GET through the permission table). Every role may today; checked anyway, so a
    future rule that narrows a read also narrows its count, here and on the event stream."""
    return all(decide("GET", c.path, {}, role).allowed for c in PAGES[page].values())


def install(app: FastAPI, *, current_session, problem) -> None:
    """Add `GET /api/summary/{page}` to `app`, and keep the cached computation on `app.state.summary_page` (an async function of the page name)
    for the event stream. `problem` is main.py's RFC 7807 answer builder (an unknown page is a 404)."""
    cache: dict[str, _Cached] = {}
    locks: dict[str, asyncio.Lock] = {}

    async def count(name: str, spec: Count) -> tuple[str, int | float | None, bool]:
        """(`name`, the value, whether the module answered). The value is None when the module could not be asked or answered without one (then
        `answered` is false), or when a `field` count's module answered null (then it is true). Never raises."""
        params = [*spec.params] if spec.field else [*spec.params, ("limit", "1")]
        if spec.since_hours is not None:
            since = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=spec.since_hours)
            params.append(("since", since.strftime("%Y-%m-%dT%H:%M:%SZ")))
        try:
            resp = await app.state.gateway.request("GET", spec.path, params=params, timeout=COUNT_TIMEOUT_SECONDS)
            body = resp.json() if resp.status_code == 200 else None
        except (SmoAuthError, httpx.HTTPError, ValueError) as exc:
            log.warning("summary count %s (%s) failed: %r", name, spec.path, exc)
            return name, None, False
        if not isinstance(body, dict):
            return name, None, False
        if spec.field:
            # A field the answer does not carry at all is an older build of the module (not a null value): counted as missing.
            value = body.get(spec.field)
            if spec.field not in body or (value is not None and (isinstance(value, bool) or not isinstance(value, (int, float)))):
                return name, None, False
            return name, value, True
        total = body.get("total")
        return (name, total, True) if isinstance(total, int) and not isinstance(total, bool) else (name, None, False)

    async def compute(page: str) -> dict:
        """Ask every count of `page` in parallel; the body the route answers."""
        names = list(PAGES[page])
        results = await asyncio.gather(*(count(n, PAGES[page][n]) for n in names))
        return {"page": page, "computedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "counts": {n: v for n, v, _ in results},
                "partial": sorted({PAGES[page][n].path.split("/")[1] for n, _, answered in results if not answered})}

    async def page_counts(page: str) -> dict:
        """The body of `page` (a key of `PAGES`), at most `CACHE_SECONDS` old: from the cache, or computed once for every caller waiting on it."""
        hit = cache.get(page)
        if hit and time.monotonic() - hit.at < CACHE_SECONDS:
            return hit.body
        # One computation per page at a time: fifty operators opening the Dashboard together cost one fan-out, not fifty.
        lock = locks.setdefault(page, asyncio.Lock())
        async with lock:
            hit = cache.get(page)
            if hit and time.monotonic() - hit.at < CACHE_SECONDS:
                return hit.body
            body = await compute(page)
            cache[page] = _Cached(time.monotonic(), body)
            return body

    app.state.summary_page = page_counts

    @app.get("/api/summary/{page}")
    async def summary(page: str, session=Depends(current_session)):
        """The counts of one console page (`PAGES`), at most `CACHE_SECONDS` old and shared by every user. `counts` maps a key such as
        `alarms.critical` to its total (null when its module did not answer); `partial` lists the modules that did not. 404 for an unknown page."""
        if page not in PAGES:
            return problem(404, "NO_SUCH_SUMMARY", f"pages: {', '.join(sorted(PAGES))}")
        if not page_allowed(page, session.user.role):
            return problem(403, "FORBIDDEN", "a count on this page needs a read your role does not have")
        return await page_counts(page)
