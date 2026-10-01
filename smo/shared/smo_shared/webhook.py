"""Shared outbound-webhook dispatch for every caller-registered
notification/callback destination in this build (DME's producer/type
callbacks, SME's event subscriptions, AIMgF's job-completion and guard
notifications, A1-Related/FOCOM/MDAF subscription callbacks, Intent
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
from urllib.parse import urlsplit

import httpx

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
    parts = urlsplit(destination)
    if parts.scheme not in _ALLOWED_SCHEMES or not parts.hostname:
        return False
    host = parts.hostname.lower()
    if host in _BLOCKED_HOSTNAMES:
        return False
    return not _is_blocked_literal_ip(host)


def post_webhook(destination: str | None, json: dict, timeout: float = 5.0) -> httpx.Response | None:
    """Best-effort POST to a caller-registered notification destination.
    No-ops — same as every call site's own pre-existing `except
    httpx.HTTPError: pass` already did for an unreachable destination —
    when the destination is missing or disallowed.
    """
    if not is_safe_webhook_destination(destination):
        if destination:
            log.warning("webhook destination %r rejected by SSRF guard; notification dropped", destination)
        return None
    try:
        return httpx.post(destination, json=json, timeout=timeout)
    except httpx.HTTPError:
        return None


def get_webhook(destination: str | None, timeout: float = 5.0) -> httpx.Response | None:
    """Best-effort GET against a caller-registered callback destination
    (e.g. a producer health check). Same no-op semantics as post_webhook.
    """
    if not is_safe_webhook_destination(destination):
        return None
    try:
        return httpx.get(destination, timeout=timeout)
    except httpx.HTTPError:
        return None


def delete_webhook(destination: str | None, timeout: float = 5.0) -> httpx.Response | None:
    """Best-effort DELETE against a caller-registered callback destination
    (e.g. telling a producer to stop a job). Same no-op semantics as
    post_webhook.
    """
    if not is_safe_webhook_destination(destination):
        return None
    try:
        return httpx.delete(destination, timeout=timeout)
    except httpx.HTTPError:
        return None
