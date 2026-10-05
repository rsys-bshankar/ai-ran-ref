"""The per-rApp kill switch, as the gateway enforces it (AI-10.4, extended).

RAN NF OAM holds the switch (`rapp_kill`, keyed on the invoker id) and refuses a stopped rApp's *config jobs* the moment it is thrown. That left
everything else a stopped rApp could still do through the gateway: publish data, start training jobs, post intents. R1 Termination now reads the
same table and refuses every *change* by a stopped rApp (and by an SMO module acting on its behalf, `X-R1-On-Behalf-Of`) with 403 `RAPP_KILLED`,
before any backend is called. What it does not refuse, so that a stopped rApp can be wound down rather than only frozen:

  - DELETE, which withdraws what the rApp made (a data job, a subscription, an intent);
  - the token endpoint (`/sme/oauth2/*`), so it can still authenticate;
  - rolling back a config job (`/ran-nf-oam/config-jobs/{id}/rollback`), as at RAN NF OAM (halting and aborting are operator actions, never an rApp's).

A config job (`POST /ran-nf-oam/config-jobs`) is not refused here either: RAN NF OAM refuses it with the same 403 `RAPP_KILLED` and, unlike the gateway, records the
refusal (`safeguard_refusals`) and tells those subscribed to it (AI-10.6). Refused here first, a stopped rApp's attempt would leave no record.

Reads are not refused: a stopped rApp that cannot read would only spin; what it can read is the question of PR-SEC-10.

The gateway looks the invoker up in the database, at most once per `R1_KILL_CACHE_SECONDS` (default 3) for each invoker, so a switch thrown at RAN NF
OAM bites at the gateway within that time. A database that cannot be read does not mean "not stopped": the last answer is used for up to a minute,
and with none the change is refused (503 `KILL_SWITCH_UNAVAILABLE`), since the modules behind the gateway need the same database. `R1_KILL_SWITCH=off`
turns the gateway's enforcement off (RAN NF OAM's own keeps working).
"""

import os
import re
import threading
import time

from sqlalchemy import Column, MetaData, String, Table, select

# the same table as ran-nf-oam/app/models.py RAppKill (the columns the gateway reads); its own metadata, so it is not part of the shared Base
METADATA = MetaData()
# RAN NF OAM's table lives in its own schema once the database has per-module schemas (PR-DB-2.7): `R1_KILL_SWITCH_SCHEMA` names it (compose and the chart set it to
# `ran_nf_oam`). Unset, the name is unqualified and resolves through the search path (the unit tests' SQLite, and a database that has not been migrated that far).
RAPP_KILL = Table("rapp_kill", METADATA, Column("invoker_id", String, primary_key=True), Column("reason", String), Column("killed_by", String),
                  schema=os.environ.get("R1_KILL_SWITCH_SCHEMA") or None)

STALE_SECONDS = 60.0
MAX_CACHED = 10_000
_EXEMPT = (
    ("POST", "/sme", re.compile(r"^/oauth2/(token|introspect)$")),
    ("POST", "/ran-nf-oam", re.compile(r"^/config-jobs/[^/]+/rollback$")),
    ("POST", "/ran-nf-oam", re.compile(r"^/config-jobs$")),          # refused, and recorded, by RAN NF OAM itself (see above)
)

_cache: dict[str, tuple[float, bool]] = {}
_lock = threading.Lock()


class KillSwitchUnavailable(RuntimeError):
    """The switch could not be read and there is no recent answer."""


def enforced(environ=os.environ) -> bool:
    return environ.get("R1_KILL_SWITCH", "on").strip().lower() not in ("off", "false", "0", "no")


def exempt(module: str, method: str, path: str) -> bool:
    """A change the gateway does not refuse as stopped: withdrawing (DELETE), authenticating, undoing a config job, and the config job RAN NF OAM refuses itself."""
    method = method.upper()
    if method == "DELETE":
        return True
    path = "/" + path.lstrip("/")
    return any(m == method and mod == module and pattern.match(path) for m, mod, pattern in _EXEMPT)


def _ttl() -> float:
    try:
        return max(0.0, float(os.environ.get("R1_KILL_CACHE_SECONDS", "3")))
    except ValueError:
        return 3.0


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def is_killed(invoker_id: str, now=time.monotonic) -> bool:
    """Whether `invoker_id` is stopped. Raises KillSwitchUnavailable when the table cannot be read and no answer is recent enough to use."""
    from . import db
    moment, ttl = now(), _ttl()
    with _lock:
        cached = _cache.get(invoker_id)
    if cached is not None and moment - cached[0] < ttl:
        return cached[1]
    try:
        with db.SessionLocal() as session:
            killed = session.execute(select(RAPP_KILL.c.invoker_id).where(RAPP_KILL.c.invoker_id == invoker_id)).first() is not None
    except Exception as exc:
        if cached is not None and moment - cached[0] < STALE_SECONDS:
            return cached[1]
        raise KillSwitchUnavailable(str(exc)) from exc
    with _lock:
        if len(_cache) >= MAX_CACHED:
            _cache.clear()
        _cache[invoker_id] = (moment, killed)
    return killed
