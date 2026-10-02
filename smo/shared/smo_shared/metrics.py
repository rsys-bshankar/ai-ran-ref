"""Prometheus metrics for every service (PR-OBS-2).

    install_metrics(app)   an HTTP middleware plus `GET /metrics` in the Prometheus text format

Two series per service, labelled by method, **route template** (`/models/{model_id}`, never the raw path, so the
label set stays bounded) and status code:

  smo_http_requests_total{method,route,status}
  smo_http_request_duration_seconds{method,route,status}   histogram

Unmatched paths are one `route="unmatched"` series. Probes and `/metrics` itself are not counted: a scrape every
15 s would otherwise be most of the traffic.

`/metrics` is for the compose / cluster network only. Prometheus scrapes each container directly; R1 Termination
refuses `/<module>/metrics` so a token holder cannot read another module's series through the gateway, and the TLS
edge does not forward `/metrics`. R1's own `/metrics` is on its container port, which `docker-compose.yml` publishes
for development; production publishes only the edge (`PR-SEC-9`).

Metrics are held per process. With `UVICORN_WORKERS` above 1 each worker answers with its own counters and a scrape
sees one of them; keep one worker per container and scale replicas (the default), or add multiprocess mode first.
"""

import time

from fastapi import FastAPI, Response
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, Histogram, generate_latest

from .logconfig import PROBE_PATHS

METRICS_PATH = "/metrics"

REQUESTS = Counter("smo_http_requests_total", "HTTP requests handled, by method, route template and status.",
                   ["method", "route", "status"])
DURATION = Histogram("smo_http_request_duration_seconds", "HTTP request duration, by method, route template and status.",
                     ["method", "route", "status"])


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

    @app.get(METRICS_PATH, include_in_schema=False)
    def metrics():
        return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
