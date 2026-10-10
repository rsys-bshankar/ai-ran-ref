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

The literal-address check is done on the address as a client would read it, not as the text happens to look: a trailing dot (`localhost.`), the
short and numeric spellings `inet_aton` accepts (`127.1`, `2130706433`, `0x7f.0.0.1`, `0177.0.0.1`, `0`), full-width and ideographic-dot spellings that an
IDNA-aware client folds to ASCII, and an IPv4 address wrapped in IPv6 (`::ffff:127.0.0.1`, 6to4, NAT64) are all reduced to the IPv4 or IPv6 address they
mean before the blocked ranges are applied (`_literal_ip`).

`is_safe_webhook_destination` does not resolve DNS: unit tests across this build register fictional subscriber hostnames (`http://consumer/cb`,
`http://rapp-1/cb`, ...) that do not resolve outside a real Docker Compose network, and the check also runs at enqueue time, where a lookup would be a
network call per registration. The send itself (`_send`, `forward_to_destination`) does resolve the host and refuses the call when any address it resolves
to is blocked, so a name that points at loopback or the metadata address is not called. A name that does not resolve is let through as before (the call
then fails by itself). Residual risk, registered in OPEN_ITEMS.md: the resolver is asked once for the check and again by the HTTP client when it connects,
so a name whose records change between the two (DNS rebinding) is not caught; closing that needs the client to connect to the vetted address, which
the module-level `httpx` calls the callers and their tests rely on do not allow.
"""

import asyncio
import ipaddress
import logging
import re
import socket
import time
import unicodedata
from urllib.parse import urlsplit

import httpx

from . import metrics, mtls

log = logging.getLogger(__name__)

_ALLOWED_SCHEMES = ("http", "https")
_BLOCKED_HOSTNAMES = frozenset({"localhost", "localhost.localdomain", "ip6-localhost", "ip6-loopback", "metadata.google.internal", "metadata"})
_BLOCKED_SUFFIXES = (".localhost",)                  # RFC 6761: every name under localhost is loopback
_DOT_VARIANTS = str.maketrans({"\u3002": ".", "\uff0e": ".", "\uff61": "."})   # dots an IDNA-aware client folds to "."
_NUMERIC_LABEL = re.compile(r"(0[xX][0-9a-fA-F]*|[0-9]+)")
_NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")


def _canonical_host(host: str) -> str:
    """The host as a resolver sees it: lower case, compatibility-folded (full-width digits, ideographic dots) and without trailing dots, so `LOCALHOST.`
    and `localhost` are the same name."""
    return unicodedata.normalize("NFKC", host).translate(_DOT_VARIANTS).lower().rstrip(".")


def _parse_inet_aton(host: str) -> ipaddress.IPv4Address | None:
    """The IPv4 address `inet_aton` reads from `host` (one to four dot-separated numbers, each decimal, `0x` hexadecimal or `0`-prefixed octal; the last
    number fills the remaining bytes), or None when `host` is not written that way.

    Raises ValueError for a host made only of numeric labels that is not a valid address (`256.1`, `08`, `1.2.3.4.5`): no real host name looks like that,
    and a client library may still read it as an address, so the caller treats it as blocked.
    """
    labels = host.split(".")
    if not 1 <= len(labels) <= 4 or not all(_NUMERIC_LABEL.fullmatch(label) for label in labels):
        if labels and all(_NUMERIC_LABEL.fullmatch(label) for label in labels):
            raise ValueError("too many numeric labels")
        return None
    numbers = []
    for label in labels:
        try:
            if label[:2] in ("0x", "0X"):
                numbers.append(int(label[2:] or "0", 16))
            elif len(label) > 1 and label[0] == "0":
                numbers.append(int(label, 8))
            else:
                numbers.append(int(label, 10))
        except ValueError as exc:                     # a digit 8 or 9 in an octal-looking label
            raise ValueError("bad numeric label") from exc
    *leading, last = numbers
    if any(n > 255 for n in leading) or last >= 256 ** (5 - len(numbers)):
        raise ValueError("numeric label out of range")
    value = last
    for position, n in enumerate(leading):
        value |= n << (8 * (3 - position))
    return ipaddress.IPv4Address(value)


def _literal_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """The IP address `host` denotes when it is written as one, in any spelling a client accepts; None for an ordinary host name.

    Raises ValueError for numeric-looking text that is not a valid address (see `_parse_inet_aton`). An IPv6 literal may carry a zone (`fe80::1%eth0`),
    which is dropped.
    """
    host = _canonical_host(host)
    if ":" in host:
        try:
            return ipaddress.ip_address(host.split("%", 1)[0])
        except ValueError as exc:
            raise ValueError("not an IPv6 address") from exc
    return _parse_inet_aton(host)


def _is_blocked_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True for an address that is never a legitimate callback target: loopback, link-local (including 169.254.169.254), multicast, unspecified or
    reserved. An IPv6 address that carries an IPv4 one (IPv4-mapped `::ffff:a.b.c.d`, 6to4 `2002::/16`, NAT64 `64:ff9b::/96`) is judged by the IPv4
    address inside it. Private ranges are not blocked: real deployments' containers sit in them."""
    if isinstance(ip, ipaddress.IPv6Address):
        embedded = ip.ipv4_mapped or ip.sixtofour or (ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF) if ip in _NAT64_PREFIX else None)
        if embedded is not None and _is_blocked_address(embedded):
            return True
    return ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved


def _is_blocked_literal_ip(host: str) -> bool:
    """True when `host` is an IP address, in any spelling, that is never a legitimate callback target (see `_is_blocked_address`), or numeric text that
    is not a valid address at all.

    False for a hostname (not resolved here) and for private-range addresses, which real deployments use.
    """
    try:
        ip = _literal_ip(host)
    except ValueError:
        return True                                   # numeric-looking but not an address: nothing legitimate is written like this
    return ip is not None and _is_blocked_address(ip)


def _is_blocked_hostname(host: str) -> bool:
    """True for a name that is loopback or metadata by definition (`localhost`, `localhost.`, `app.localhost`, `metadata.google.internal`)."""
    name = _canonical_host(host)
    return name in _BLOCKED_HOSTNAMES or name.endswith(_BLOCKED_SUFFIXES)


def _resolved_addresses(host: str, port: int | None) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    """Every address the system resolver gives for `host`; an empty list when the name does not resolve (the call then fails by itself)."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError):
        return []
    found = []
    for info in infos:
        try:
            found.append(ipaddress.ip_address(str(info[4][0]).split("%", 1)[0]))
        except ValueError:
            continue
    return found


def _resolves_to_blocked_address(destination: str | None) -> bool:
    """True when the host of `destination` resolves to at least one blocked address. Every address counts, not the first: a name with one public and one
    loopback record could be connected to on either. A literal IP needs no lookup (the literal check has judged it), and a name that does not resolve is
    not blocked here."""
    try:
        parts = urlsplit(destination or "")
        host, port = parts.hostname, parts.port
    except ValueError:
        return True
    if not host:
        return True
    try:
        if _literal_ip(host) is not None:
            return False
    except ValueError:
        return True
    return any(_is_blocked_address(ip) for ip in _resolved_addresses(host, port))


def is_safe_webhook_destination(destination: str | None) -> bool:
    """True when `destination` may be called: a non-empty `http` or `https` URL with a host that is not one of `localhost`, `metadata.google.internal`,
    `metadata` (or a name under `.localhost`, with or without a trailing dot) and not a blocked literal IP in any spelling (`127.1`, `2130706433`,
    `0x7f.0.0.1`, `::ffff:127.0.0.1`, ...).

    False for None, an empty or malformed URL, any other scheme or a missing host. This is the SSRF guard every outbound callback passes (the outbox
    checks it at enqueue and again at send). It does not resolve names (the send does, see `_send`).
    """
    if not destination:
        return False
    try:
        parts = urlsplit(destination)
        hostname = parts.hostname
    except ValueError:  # malformed, e.g. an unclosed IPv6 bracket ("http://[")
        return False
    if parts.scheme not in _ALLOWED_SCHEMES or not hostname:
        return False
    if _is_blocked_hostname(hostname):
        return False
    return not _is_blocked_literal_ip(hostname)


def _send(method: str, destination: str | None, **kwargs) -> httpx.Response | None:
    """The one place a callback leaves the process (PR-OBS-2.6): counted by outcome under the constant target `callback`, never by host
    (the destination is caller-supplied, so a host label would be unbounded)."""
    if not is_safe_webhook_destination(destination) or _resolves_to_blocked_address(destination):
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
    if not value or len(value) > 300 or value != value.strip():
        return None
    if any(ord(c) < 33 or ord(c) == 127 for c in value) or any(c in value for c in "?#\\%"):
        return None
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None
    if parts.username is not None or parts.password is not None or port == 0 or ".." in parts.path or "//" in parts.path:
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
    # the lookup blocks, so it runs in a thread: this coroutine serves the gateway's event loop
    if not is_safe_webhook_destination(destination) or await asyncio.to_thread(_resolves_to_blocked_address, destination):
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
