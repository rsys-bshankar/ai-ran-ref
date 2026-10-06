"""Structured JSON logs for every service (PR-OBS-1).

One JSON object per line on stdout, which is what a log shipper (Loki, Fluent Bit, CloudWatch, `kubectl logs | jq`)
parses without a regular expression:

    {"timestamp": "2026-10-02T16:45:12.034Z", "level": "INFO", "logger": "app.main", "service": "nfo",
     "correlationId": "5b0f...", "message": "request", "method": "POST", "route": "/deployments", "status": 201, "durationMs": 12.4}

  `configure_logging()`   installs the one handler on the root logger, sets the level from `LOG_LEVEL` (default
                          INFO), and sends uvicorn's own logs through it (its plain-text access line is turned off:
                          the access-log middleware below replaces it, with the route template and the correlation id).
  `install_logging(app)`  configure_logging() plus `AccessLogMiddleware`: one line per request with the method, the route
                          template (`/models/{model_id}`, never the raw path or the query string, which may carry an id or a
                          token), the status and the duration in milliseconds. Probes (`/live`, `/ready`, `/health`) are logged
                          at DEBUG so a probe every few seconds does not drown the log; 5xx answers are ERROR.
  Every record carries `service` (the `MODULE` of the container) and, inside a request, `correlationId` (the one
  `X-Correlation-ID` propagates through the whole fan-out, `correlation.py`) and, when the request belongs to a trace, `traceId` (`tracing.py`). Extra fields passed with
  `log.info("msg", extra={"deploymentId": ...})` become JSON keys.

Redaction (`RedactionFilter`, on the handler, so it covers every logger including uvicorn's and third-party ones):
a log line must never contain a credential. Before a record is formatted, the message, the exception text and every extra
field are scrubbed: `Authorization` / `Bearer` values, `password=`, `secret=`, `token=`, `api_key=` and similar `key: value`
and `"key": "value"` pairs, the password in a `scheme://user:password@host` URL, and any extra field whose name says it is a
secret. It is pattern based, so it is a safety net, not permission to log a credential: do not log one.
"""

import datetime
import json
import logging
import os
import re
import sys
import time

from .correlation import HEADER_NAME as CORRELATION_ID_HEADER
from .correlation import get_correlation_id
from .tracing import SCOPE_TRACE_ID, get_trace_id

REDACTED = "[REDACTED]"
PROBE_PATHS = frozenset({"/live", "/ready", "/health"})
_HANDLER_MARK = "_smo_json_handler"

_SECRET_NAME = r"(?:pass(?:word|wd)?|secret|client_secret|onboarding_?secret|(?:access_|refresh_|id_|session_)?token|api[_-]?key|credentials?|private[_-]?key|cookie|set-cookie)"  # noqa: S105 — a pattern of names to redact, not a credential
_PATTERNS = [
    # Authorization: Bearer abc / "authorization": "Basic abc"  (keeps the scheme, drops the value)
    (re.compile(r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?)(bearer|basic|token)\s+[^\s\"',;}]+"), r"\1\2 " + REDACTED),
    # a bare "Bearer <token>" anywhere
    (re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 " + REDACTED),
    # password=..., "secret": "...", token: ...   (stops at a quote, space, comma, semicolon, ampersand or brace)
    (re.compile(r"(?i)((?<![A-Za-z0-9])" + _SECRET_NAME + r"[\"']?\s*[:=]\s*[\"']?)[^\s\"',;&}]+"), r"\1" + REDACTED),
    # scheme://user:password@host
    (re.compile(r"(?i)(\b[a-z][a-z0-9+.-]*://[^\s/:@]+:)[^\s/@]+(@)"), r"\1" + REDACTED + r"\2"),
]
_SECRET_KEY = re.compile(r"(?i)^" + _SECRET_NAME + r"$|authorization|secret|password")


def redact_text(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact_value(key: str, value):
    """An extra field: dropped to REDACTED by name, else scrubbed inside (strings, and the strings of dicts and lists)."""
    if _SECRET_KEY.search(key):
        return REDACTED
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: redact_value(str(k), v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_value(key, v) for v in value]
    return value


_STANDARD_ATTRIBUTES = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime", "taskName"}


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact_text(record.getMessage())
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = redact_text(record.exc_text)
        for key in [k for k in vars(record) if k not in _STANDARD_ATTRIBUTES]:
            setattr(record, key, redact_value(key, getattr(record, key)))
        return True


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str | None = None):
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        moment = datetime.datetime.fromtimestamp(record.created, datetime.UTC)
        entry = {
            "timestamp": moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "service": self.service or os.environ.get("MODULE", "smo"),
            "message": record.getMessage(),
        }
        correlation_id = getattr(record, "correlationId", None) or get_correlation_id()
        if correlation_id:
            entry["correlationId"] = correlation_id
        trace_id = getattr(record, "traceId", None) or get_trace_id()
        if trace_id:
            entry["traceId"] = trace_id        # PR-OBS-3: the W3C trace id, so a log line and a Tempo trace join on it
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRIBUTES and key not in entry:
                entry[key] = value
        if record.exc_text:
            entry["exception"] = record.exc_text
        elif record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str, ensure_ascii=False)


class _StdoutHandler(logging.StreamHandler):
    """Writes to whatever `sys.stdout` is at the moment of each record, so a test runner that swaps stdout (pytest) never leaves it
    holding a closed stream."""

    def __init__(self):
        logging.Handler.__init__(self)

    @property
    def stream(self):
        return sys.stdout

    @stream.setter
    def stream(self, value):
        pass


def _level(environ=os.environ) -> tuple[int, str | None]:
    """(level, warning) from LOG_LEVEL; an unknown name is INFO, with a warning to log."""
    name = environ.get("LOG_LEVEL", "").strip().upper()
    if not name:
        return logging.INFO, None
    value = logging.getLevelName(name)
    if isinstance(value, int):
        return value, None
    return logging.INFO, f"LOG_LEVEL={environ.get('LOG_LEVEL')!r} is not a log level (DEBUG, INFO, WARNING, ERROR, CRITICAL); using INFO"


def configure_logging(service: str | None = None, stream=None, environ=os.environ) -> None:
    """Idempotent: installs (or replaces) this module's handler on the root logger; other handlers are left alone."""
    root = logging.getLogger()
    for handler in [h for h in root.handlers if getattr(h, _HANDLER_MARK, False)]:
        root.removeHandler(handler)
    handler = logging.StreamHandler(stream) if stream is not None else _StdoutHandler()
    handler.setFormatter(JsonFormatter(service))
    handler.addFilter(RedactionFilter())
    setattr(handler, _HANDLER_MARK, True)
    root.addHandler(handler)
    level, warning = _level(environ)
    root.setLevel(level)
    for name in ("uvicorn", "uvicorn.error"):          # uvicorn's own logs go through the one handler
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers = []
        uvicorn_logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True  # replaced by AccessLogMiddleware
    if warning:
        logging.getLogger(__name__).warning(warning)


class AccessLogMiddleware:
    """One structured line per HTTP request (pure ASGI, so it sees the real status and the whole duration)."""

    def __init__(self, app):
        self.app = app
        self.log = logging.getLogger("smo.access")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        started = time.perf_counter()
        status, correlation_id = 500, None

        async def recording_send(message):
            nonlocal status, correlation_id
            if message["type"] == "http.response.start":
                status = message["status"]
                for name, value in message.get("headers", []):
                    if name.lower() == CORRELATION_ID_HEADER.lower().encode():
                        correlation_id = value.decode()
            await send(message)

        try:
            await self.app(scope, receive, recording_send)
        finally:
            route = scope.get("route")
            template = getattr(route, "path", None) or "unmatched"
            level = logging.DEBUG if scope["path"] in PROBE_PATHS else (logging.ERROR if status >= 500 else logging.INFO)
            extra = {"method": scope["method"], "route": template, "status": status,
                     "durationMs": round((time.perf_counter() - started) * 1000, 2)}
            if correlation_id:
                extra["correlationId"] = correlation_id
            if scope.get(SCOPE_TRACE_ID):
                extra["traceId"] = scope[SCOPE_TRACE_ID]
            self.log.log(level, "request", extra=extra)


def install_logging(app, service: str | None = None) -> None:
    """What each service's `main.py` calls: configure logging (unless something already did) and log every request."""
    if not any(getattr(h, _HANDLER_MARK, False) for h in logging.getLogger().handlers):
        configure_logging(service)
    app.add_middleware(AccessLogMiddleware)
