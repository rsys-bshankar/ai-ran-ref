"""Shared outbound-webhook dispatch for every caller-registered
notification/callback destination in this build (DME's producer/type
callbacks, SME's event subscriptions, AIMgF's job-completion and guard
notifications, FOCOM/MDAF subscription callbacks, Intent
Service's RMIH and autonomy-dispatch notifications, ...).

CodeQL py/full-ssrf flags every one of these: each destination is
caller-supplied (set at subscribe/register time, stored, called back
later), and a best-effort POST/GET goes out to it. A private-IP
blocklist is the textbook SSRF mitigation, but a *hostname* allowlist
is not viable here — this build's legitimate targets are frequently
other rApp/producer containers whose hostnames are assigned at deploy
time (NFO) and are never known in advance (HISTORY.md OI-6.3's own
PR #138 discussion). So the barrier this module applies is the
part of SSRF mitigation that doesn't depend on knowing the target set
up front: block the scheme (http/https only — no file:///, gopher://,
data:, ...) and block the handful of literal addresses that are never
a legitimate webhook target anywhere (loopback, link-local — which
includes the 169.254.169.254 cloud metadata endpoint, multicast,
unspecified/reserved). Everything else — any other hostname, any other
private-range IP a real deployment's containers sit on — passes
through unchanged, same as before this module existed.

This does not resolve DNS before deciding: unit tests across this
build register fictional subscriber hostnames (`http://consumer/cb`,
`http://rapp-1/cb`, ...) that don't resolve outside a real Docker
Compose network, and resolving them here would either break every one
of those tests or require a live network call on every notification.
A literal dangerous IP is still blocked outright; a hostname that
*resolves* to one (DNS rebinding) is a known, accepted residual risk —
the same one essentially every service in this reference build already
carries by calling caller-chosen hostnames at all.
"""

import ipaddress
import logging
import time
from urllib.parse import urlsplit

import httpx

from . import metrics, mtls

log = logging.getLogger(__name__)

_ALLOWED_SCHEMES = ("http", "https")
_BLOCKED_HOSTNAMES = frozenset({"localhost", "metadata.google.internal", "metadata"})


def _is_blocked_literal_ip(host: str) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False  # not a literal IP address — an ordinary hostname, not resolved here
    return ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved


def is_safe_webhook_destination(destination: str | None) -> bool:
    if not destination:
        return False
    try:
        parts = urlsplit(destination)
        hostname = parts.hostname
    except ValueError:  # malformed, e.g. an unclosed IPv6 bracket ("http://[")
        return False
    if parts.scheme not in _ALLOWED_SCHEMES or not hostname:
        return False
    host = hostname.lower()
    if host in _BLOCKED_HOSTNAMES:
        return False
    return not _is_blocked_literal_ip(host)

def _send(method: str, destination: str | None, **kwargs) -> httpx.Response | None:
    """The one place a callback leaves the process (PR-OBS-2.6): counted by outcome under the constant target `callback`, never by host
    (the destination is caller-supplied, so a host label would be unbounded)."""
    if not is_safe_webhook_destination(destination):
        if destination and method == "post":
            log.warning("webhook destination %r rejected by SSRF guard; notification dropped", destination)
        metrics.record_outbound("webhook", "callback", method, "blocked")
        return None
    started = time.perf_counter()
    try:
        resp = getattr(httpx, method)(destination, **mtls.webhook_kwargs(destination), **kwargs)   # PR-SEC-2: the client certificate only to an https destination inside the deployment
    except httpx.TimeoutException:
        metrics.record_outbound("webhook", "callback", method, "timeout", time.perf_counter() - started)
        return None
    except httpx.HTTPError:
        metrics.record_outbound("webhook", "callback", method, "error", time.perf_counter() - started)
        return None
    status = getattr(resp, "status_code", None)             # a stub in a test may answer with something that is not a response
    metrics.record_outbound("webhook", "callback", method, "error" if status is None else metrics.outcome_of(status),
                            time.perf_counter() - started)
    return resp


def post_webhook(destination: str | None, json: dict, timeout: float = 5.0) -> httpx.Response | None:
    """Best-effort POST to a caller-registered notification destination.
    No-ops — same as every call site's own pre-existing `except
    httpx.HTTPError: pass` already did for an unreachable destination —
    when the destination is missing or disallowed.
    """
    return _send("post", destination, json=json, timeout=timeout)


def get_webhook(destination: str | None, timeout: float = 5.0) -> httpx.Response | None:
    """Best-effort GET against a caller-registered callback destination
    (e.g. a producer health check). Same no-op semantics as post_webhook.
    """
    return _send("get", destination, timeout=timeout)


def delete_webhook(destination: str | None, timeout: float = 5.0) -> httpx.Response | None:
    """Best-effort DELETE against a caller-registered callback destination
    (e.g. telling a producer to stop a job). Same no-op semantics as
    post_webhook.
    """
    return _send("delete", destination, timeout=timeout)


def normalise_base_url(value: str | None) -> str | None:
    """A base URL a workload or an operator registers for calls the platform makes later (a rApp instance's `operatorApiBase`, PR-GUI-8): the origin
    plus an optional path prefix, `http` or `https`, no credentials, query or fragment, at most 300 characters, passing `is_safe_webhook_destination`.
    Returns it without a trailing slash, or None when it is not acceptable. The same guard runs again before every call (`forward_to_destination`)."""
    if not value or len(value) > 300 or value != value.strip() or any(ord(c) < 33 or ord(c) == 127 for c in value):
        return None
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment or "?" in value or "#" in value:
        return None
    if port == 0 or ".." in parts.path or "%" in parts.path or "//" in parts.path or "\\" in value:
        return None
    if not is_safe_webhook_destination(value):
        return None
    return value.rstrip("/")


async def forward_to_destination(method: str, destination: str | None, *, headers: dict, params, content: bytes | None,
                                 timeout: float) -> httpx.Response | None:
    """One forwarded call (any method, with headers, query and body) to a caller-registered base URL, for the gateway's operator-API prefix (PR-GUI-8).

    Not a notification, so not a row of docs/NOTIFICATIONS.md's inventory, but the same guard: a destination that fails `is_safe_webhook_destination` is
    not called (None: the caller answers 502 without saying why) and an https destination inside the deployment gets the client certificate
    (`mtls.webhook_kwargs`). A timeout raises `httpx.TimeoutException` and another transport failure `httpx.HTTPError`, which the caller maps to a fixed
    504 / 502 body: neither the exception text nor the destination is ever put in a response.
    """
    if not is_safe_webhook_destination(destination):
        metrics.record_outbound("webhook", "operator-api", method.lower(), "blocked")
        return None
    started = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout, **mtls.webhook_kwargs(destination)) as client:
            resp = await client.request(method, str(destination), headers=headers, params=params, content=content)
    except httpx.TimeoutException:
        metrics.record_outbound("webhook", "operator-api", method.lower(), "timeout", time.perf_counter() - started)
        raise
    except httpx.HTTPError:
        metrics.record_outbound("webhook", "operator-api", method.lower(), "error", time.perf_counter() - started)
        raise
    metrics.record_outbound("webhook", "operator-api", method.lower(), metrics.outcome_of(resp.status_code), time.perf_counter() - started)
    return resp
