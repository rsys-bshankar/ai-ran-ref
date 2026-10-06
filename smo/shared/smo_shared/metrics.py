"""Prometheus metrics for every service (PR-OBS-2).

    install_metrics(app)   an HTTP middleware plus `GET /metrics` in the Prometheus text format

Two series per service, labelled by method, **route template** (`/models/{model_id}`, never the raw path, so the
label set stays bounded) and status code:

  smo_http_requests_total{method,route,status}
  smo_http_request_duration_seconds{method,route,status}   histogram

Two more families come from the shared library itself (PR-OBS-2.4, 2.5):

  smo_fsm_transitions_total{machine,from_state,event,to_state}   every transition a state machine takes
  smo_fsm_illegal_transitions_total{machine,from_state,event}    every refused one
  smo_db_pool_connections{state}                                 in_use / idle / overflow, read at scrape time
  smo_db_pool_capacity                                           pool_size + max_overflow

`machine` is the name of the state enum (`JobState`, `ModelLifecycleState`, ...), so the label set is the declared states and
events and nothing else. SQLAlchemy does not expose how many callers are waiting for a connection, so there is no "waiting" gauge: a
pool that is exhausted shows as `in_use` equal to `smo_db_pool_capacity`.

Outbound calls (PR-OBS-2.6) are counted where they leave the process:

  smo_outbound_calls_total{client,target,method,outcome}
  smo_outbound_call_duration_seconds{client,target}      histogram

`client` is `r1` (a call through R1 Termination, `R1Client`) or `webhook` (a caller-registered callback, `smo_shared.webhook`). `target`
for `r1` is the module in the path (`sme`, `dme`, ...), a small fixed set; for `webhook` it is the constant `callback`, **never the
host**: the destination is whatever a caller registered, so a host label would let any caller grow the series without bound. `outcome` is
`2xx`/`3xx`/`4xx`/`5xx`, `timeout`, `error` (could not connect or any other transport failure) or `blocked` (the SSRF guard refused the
destination, nothing was sent). R1Client counts each attempt, so the one retry after a 401 shows as two calls.

Unmatched paths are one `route="unmatched"` series. Probes and `/metrics` itself are not counted: a scrape every
15 s would otherwise be most of the traffic.

`/metrics` is for the compose / cluster network only. Prometheus scrapes each container directly; R1 Termination
refuses `/<module>/metrics` so a token holder cannot read another module's series through the gateway, and the TLS
edge does not forward `/metrics`. R1's own `/metrics` is on its container port, which `docker-compose.yml` publishes
for development; production publishes only the edge (`PR-SEC-9`).

Metrics are held per process. With `UVICORN_WORKERS` above 1 each worker answers with its own counters and a scrape
sees one of them; keep one worker per container and scale replicas (the default), or add multiprocess mode first.
"""

import re
import sys
import time

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, Histogram, generate_latest
from prometheus_client.core import GaugeMetricFamily

from .logconfig import PROBE_PATHS

METRICS_PATH = "/metrics"

REQUESTS = Counter("smo_http_requests_total", "HTTP requests handled, by method, route template and status.",
                   ["method", "route", "status"])
DURATION = Histogram("smo_http_request_duration_seconds", "HTTP request duration, by method, route template and status.",
                     ["method", "route", "status"])


FSM_TRANSITIONS = Counter("smo_fsm_transitions_total", "State machine transitions taken, by machine, from state, event and to state.",
                          ["machine", "from_state", "event", "to_state"])
FSM_ILLEGAL = Counter("smo_fsm_illegal_transitions_total", "State machine transitions refused, by machine, from state and event.",
                      ["machine", "from_state", "event"])


def _name(value) -> str:
    """An enum member's value (or the plain string): the label, never an object repr."""
    return str(getattr(value, "value", value))


def record_transition(machine: str, from_state, event, to_state) -> None:
    FSM_TRANSITIONS.labels(machine, _name(from_state), _name(event), _name(to_state)).inc()


def record_illegal_transition(machine: str, from_state, event) -> None:
    FSM_ILLEGAL.labels(machine, _name(from_state), _name(event)).inc()


ROLE_REFUSALS = Counter("smo_role_refusals_total", "Calls an rApp made to a route only SMO modules may call (PR-SEC-14), by R1 module and what was done.",
                        ["module", "action"])


def record_role_refusal(module: str, action: str) -> None:
    ROLE_REFUSALS.labels(r1_target(module), action).inc()


RATE_STORE_ERRORS = Counter("smo_rate_store_errors_total", "Statements against the shared rate limiter store that failed (PR-SEC-8.5; the limiter then fails open).")
RATE_STORE_FALLBACKS = Counter("smo_rate_store_fallbacks_total", "Requests the limiter decided with the per-replica bucket because the shared store was unavailable (PR-SEC-8.5).")


def record_rate_store_error() -> None:
    RATE_STORE_ERRORS.inc()


def record_rate_store_fallback() -> None:
    RATE_STORE_FALLBACKS.inc()


AUDIT_WRITES = Counter("smo_audit_writes_total", "Rows the gateway tried to add to the audit chain (PR-SEC-11), by outcome (ok or failed).", ["outcome"])


def record_audit_write(outcome: str) -> None:
    AUDIT_WRITES.labels(outcome).inc()


OUTBOUND_CALLS = Counter("smo_outbound_calls_total", "Outbound calls, by client (r1 or webhook), target, method and outcome.",
                         ["client", "target", "method", "outcome"])
OUTBOUND_DURATION = Histogram("smo_outbound_call_duration_seconds", "Outbound call duration, by client and target.", ["client", "target"])

_TARGET_OK = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")


def r1_target(path: str) -> str:
    """The module a path through R1 addresses (`/sme/x` is `sme`); anything that is not a module-shaped segment is `other`."""
    segment = path.lstrip("/").split("/", 1)[0].split("?", 1)[0]
    return segment if _TARGET_OK.match(segment) else "other"


def outcome_of(status: int) -> str:
    return f"{status // 100}xx"


def record_outbound(client: str, target: str, method: str, outcome: str, seconds: float | None = None) -> None:
    OUTBOUND_CALLS.labels(client, target, method.upper(), outcome).inc()
    if seconds is not None:
        OUTBOUND_DURATION.labels(client, target).observe(seconds)


class PoolCollector:
    """Connection pool gauges, read from the engine's pool when Prometheus scrapes, so the request path pays nothing."""

    def __init__(self, engine_getter):
        self._engine_getter = engine_getter

    def collect(self):
        pool = getattr(self._engine_getter(), "pool", None)
        if pool is None or not all(hasattr(pool, attr) for attr in ("checkedout", "checkedin", "overflow", "size")):
            return                                                  # SQLite's test pools are not a QueuePool: no series
        in_use = GaugeMetricFamily("smo_db_pool_connections", "Database pool connections, by state.", labels=["state"])
        in_use.add_metric(["in_use"], pool.checkedout())
        in_use.add_metric(["idle"], pool.checkedin())
        in_use.add_metric(["overflow"], max(0, pool.overflow()))
        yield in_use
        capacity = GaugeMetricFamily("smo_db_pool_capacity", "Most connections the pool may hold: pool_size plus max_overflow.")
        capacity.add_metric([], pool.size() + getattr(pool, "_max_overflow", 0))
        yield capacity


def _modules_engine():
    """The module's own engine if this process has one. Never imports `smo_shared.db`: building that engine needs database credentials,
    and R1 Termination and the mock services have none (importing it here took them down)."""
    db = sys.modules.get("smo_shared.db")
    return getattr(db, "engine", None)


_pool_collector: PoolCollector | None = None


def register_pool_metrics(engine_getter) -> None:
    """Register the pool gauges once per process; `engine_getter` is called at each scrape."""
    global _pool_collector
    if _pool_collector is None:
        _pool_collector = PoolCollector(engine_getter)
        REGISTRY.register(_pool_collector)


class MetricsMiddleware:
    """Pure ASGI, like the access log: it sees the real status and the whole duration."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in PROBE_PATHS or scope["path"] == METRICS_PATH:
            return await self.app(scope, receive, send)
        started = time.perf_counter()
        status = 500

        async def recording_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, recording_send)
        finally:
            template = getattr(scope.get("route"), "path", None) or "unmatched"
            labels = (scope["method"], template, str(status))
            REQUESTS.labels(*labels).inc()
            DURATION.labels(*labels).observe(time.perf_counter() - started)


def install_metrics(app: FastAPI) -> None:
    """What each service's `main.py` calls, right after `install_logging(app)`."""
    app.add_middleware(MetricsMiddleware)
    register_pool_metrics(_modules_engine)

    @app.get(METRICS_PATH, include_in_schema=False)
    def metrics():
        return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
