"""PR-OBS-3.6 / PR-OBS-6.4: a live check of the compose `tracing` and `logging` profiles.

    docker compose exec -T r1-termination python3 - < scripts/obs_smoke.py

Runs inside the R1 Termination container (standard library only), on the compose network, against a stack started as

    SMO_WITH_TRACING=1 SMO_OTEL_ENDPOINT=http://tempo:4318 docker compose --profile tracing --profile logging up -d --build

It makes ONE request through the gateway (`GET /bootstrap`, no token) carrying a W3C `traceparent` with a fresh trace id, then checks, retrying
until a deadline (spans and log lines take a few seconds to arrive):

  - Tempo answers /ready, and the trace id is found through Tempo's API with at least one span (the gateway's server span);
  - Loki answers /ready, and a log line of that request (`traceId` equal to the id, shipped by Fluent Bit) is found through Loki's API;
  - Grafana is healthy and its provisioned `tempo` and `loki` data sources report OK.

Prints what was checked and the HTTP status, never a header, a body or a token. Exits 1 if any group misses its deadline.
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import Any


def new_trace_id() -> str:
    """A random 32-hex W3C trace id that is not all zero."""
    while True:
        value = secrets.token_hex(16)
        if int(value, 16):
            return value


def make_traceparent(trace_id: str) -> str:
    """Version 00, a fresh non-zero span id, sampled flag set (so the gateway's parent-based sampler records it)."""
    span_id = secrets.token_hex(8)
    while not int(span_id, 16):
        span_id = secrets.token_hex(8)
    return f"00-{trace_id}-{span_id}-01"


def count_spans(document: Any) -> int:
    """Spans in a Tempo trace answer, whatever the envelope (v1 `batches`, v2 `trace.resourceSpans`): every list under a key named `spans`."""
    if isinstance(document, dict):
        total = 0
        for key, value in document.items():
            if key == "spans" and isinstance(value, list):
                total += len(value)
            else:
                total += count_spans(value)
        return total
    if isinstance(document, list):
        return sum(count_spans(item) for item in document)
    return 0


def is_ready(status: int, body: str) -> bool:
    """Tempo and Loki answer /ready with 200 and the word `ready`; anything else (503 `Ingester not ready`, ...) is not."""
    return status == 200 and body.strip().lower() == "ready"


def loki_query(trace_id: str) -> str:
    """The query the documentation gives for one trace (docs/OBSERVABILITY.md)."""
    return '{job="smo"} | json | traceId="' + trace_id + '"'


def loki_lines(answer: Any) -> list[tuple[dict[str, str], str]]:
    """(stream labels, line) pairs of a Loki query_range answer; [] for anything that is not a streams result."""
    try:
        data = answer["data"]
        if data.get("resultType") != "streams":
            return []
        out: list[tuple[dict[str, str], str]] = []
        for stream in data["result"]:
            labels = {str(k): str(v) for k, v in (stream.get("stream") or {}).items()}
            for value in stream.get("values", []):
                out.append((labels, str(value[1])))
        return out
    except (KeyError, TypeError, AttributeError, IndexError):
        return []


def find_request_line(answer: Any, trace_id: str) -> dict[str, Any] | None:
    """The JSON log line carrying this trace id, or None. The line must parse and carry the id itself (not just be returned by the query)."""
    for _labels, line in loki_lines(answer):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and entry.get("traceId") == trace_id:
            return entry
    return None


def datasource_ok(status: int, body: Any) -> bool:
    """Grafana's /api/datasources/uid/<uid>/health: 200 and {"status": "OK"}."""
    return status == 200 and isinstance(body, dict) and str(body.get("status", "")).upper() == "OK"


def tempo_echo_ok(status: int, body: str) -> bool:
    """Tempo's /api/echo, which is what Grafana's own "Save & test" calls through the data source proxy: 200 and the word `echo`."""
    return status == 200 and body.strip().lower() == "echo"


def grafana_healthy(status: int, body: Any) -> bool:
    """Grafana's /api/health: 200 and the database `ok`."""
    return status == 200 and isinstance(body, dict) and str(body.get("database", "")).lower() == "ok"


def retry_until(check: Callable[[], str | None], deadline_seconds: float, interval: float = 2.0,
                clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> str | None:
    """Call `check` (None = success, else a reason) until it succeeds or the deadline passes; returns None or the last reason. Always tries once."""
    end = clock() + deadline_seconds
    reason = check()
    while reason is not None and clock() < end:
        sleep(interval)
        reason = check()
    return reason


def http(method: str, url: str, headers: dict[str, str] | None = None, timeout: float = 10.0) -> tuple[int, str]:
    """(status, body text); (0, exception class name) when nothing answered. Neither request nor response headers are ever printed."""
    request = urllib.request.Request(url, method=method, headers=headers or {})  # noqa: S310 - fixed http URLs on the compose network
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")
    except (urllib.error.URLError, OSError) as error:
        return 0, type(error).__name__


def http_json(url: str) -> tuple[int, Any]:
    """GET `url` and return `(status, parsed JSON)`; the parsed value is None when the body is not JSON (including the exception name `http` returns when nothing answered)."""
    status, body = http("GET", url)
    try:
        return status, json.loads(body)
    except ValueError:
        return status, None


def main(argv: list[str] | None = None) -> int:
    """Runs the checks in the module docstring, each group retried until `--deadline` seconds have passed, and returns 1 if any group missed it.

        Order: Tempo and Loki ready; then one request through the gateway (retried until it answers 200; if it never does the rest cannot run and the function returns 1
        at once); then the trace in Tempo, the log line in Loki, Grafana healthy and its two provisioned data sources. The request is made only after both stores are
        ready because Fluent Bit tails from the end of the file, so an earlier log line could be missed.
    """
    parser = argparse.ArgumentParser(description="Live check of the compose tracing and logging profiles")
    parser.add_argument("--gateway", default="http://localhost:8000", help="R1 Termination, as seen from where this runs")
    parser.add_argument("--tempo", default="http://tempo:3200")
    parser.add_argument("--loki", default="http://loki:3100")
    parser.add_argument("--grafana", default="http://grafana:3000")
    parser.add_argument("--deadline", type=float, default=120.0, help="seconds each group may take to succeed")
    parser.add_argument("--interval", type=float, default=3.0)
    args = parser.parse_args(argv)
    failures: list[str] = []

    def group(name: str, check: Callable[[], str | None]) -> None:
        reason = retry_until(check, args.deadline, args.interval)
        print(f"{'ok  ' if reason is None else 'FAIL'} {name}" + ("" if reason is None else f"  ({reason})"), flush=True)
        if reason is not None:
            failures.append(name)

    def ready(base: str) -> Callable[[], str | None]:
        def check() -> str | None:
            status, body = http("GET", base + "/ready")
            return None if is_ready(status, body) else f"/ready answered {status}"
        return check

    group("Tempo is ready", ready(args.tempo))
    group("Loki is ready", ready(args.loki))

    # One request, made after both stores are up (Fluent Bit tails from the end of the file, so a line written earlier could be missed).
    trace_id = new_trace_id()
    traceparent = make_traceparent(trace_id)

    def request_made() -> str | None:
        code, _ = http("GET", args.gateway + "/bootstrap", {"traceparent": traceparent, "X-Correlation-ID": "obs-smoke-" + trace_id[:8]})
        return None if code == 200 else f"the gateway answered {code} on /bootstrap (R1_BOOTSTRAP_KEY set, or still starting)"

    reason = retry_until(request_made, args.deadline, args.interval)
    print(f"{'ok  ' if reason is None else 'FAIL'} request through the gateway (trace {trace_id})" + ("" if reason is None else f"  ({reason})"), flush=True)
    if reason is not None:
        return 1

    def trace_found() -> str | None:
        reasons = []
        for path in ("/api/traces/", "/api/v2/traces/"):
            code, document = http_json(args.tempo + path + trace_id)
            if code == 200 and count_spans(document) > 0:
                return None
            reasons.append(f"{path} answered {code}")
        return "; ".join(reasons) + " (spans need images built with SMO_WITH_TRACING=1 and SMO_OTEL_ENDPOINT=http://tempo:4318)"

    group("the trace is found through Tempo's API", trace_found)

    def log_found() -> str | None:
        now = time.time_ns()
        query = urllib.parse.urlencode({"query": loki_query(trace_id), "start": now - 15 * 60 * 10**9, "end": now + 60 * 10**9,
                                        "limit": 50, "direction": "backward"})
        code, answer = http_json(f"{args.loki}/loki/api/v1/query_range?{query}")
        if code != 200:
            return f"query_range answered {code}"
        return None if find_request_line(answer, trace_id) else "no log line with this traceId yet (Fluent Bit to Loki)"

    group("the request's log line is queryable through Loki's API", log_found)

    def grafana_ok() -> str | None:
        code, body = http_json(args.grafana + "/api/health")
        return None if grafana_healthy(code, body) else f"/api/health answered {code}"

    group("Grafana is healthy", grafana_ok)
    # Loki's data source implements the backend health check. Tempo's does not (Grafana answers 404 `plugin.notImplemented`; the UI's "Save & test"
    # runs in the browser and calls Tempo's /api/echo through the data source proxy), so that is what is called here.
    def loki_source_ok() -> str | None:
        code, body = http_json(f"{args.grafana}/api/datasources/uid/loki/health")
        return None if datasource_ok(code, body) else f"health answered {code}"

    def tempo_source_ok() -> str | None:
        code, body = http("GET", f"{args.grafana}/api/datasources/proxy/uid/tempo/api/echo")
        return None if tempo_echo_ok(code, body) else f"proxy to Tempo's /api/echo answered {code}"

    group("Grafana's provisioned tempo data source reaches Tempo", tempo_source_ok)
    group("Grafana's provisioned loki data source reports OK", loki_source_ok)

    print("FAILED: " + ", ".join(failures) if failures else "all observability checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
