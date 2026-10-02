"""Property-based tests (Hypothesis) for the SSRF guard in smo_shared.webhook.

The example-based cases in test_webhook.py pin known good and bad destinations;
these generate thousands of inputs to check the guard's contract: it never
raises, a non-http(s) scheme is never allowed, and an IP literal is allowed
exactly when it is not loopback / link-local / multicast / unspecified / reserved.
Run with: cd smo/shared && PYTHONPATH=. python -m pytest tests -q
"""

import ipaddress

from hypothesis import given, settings, strategies as st

from smo_shared.webhook import is_safe_webhook_destination


def _blocked(ip) -> bool:
    return ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified or ip.is_reserved


@settings(max_examples=300)
@given(st.one_of(st.none(), st.text()))
def test_the_guard_never_raises_and_returns_a_bool(destination):
    assert isinstance(is_safe_webhook_destination(destination), bool)


@settings(max_examples=200)
@given(scheme=st.sampled_from(["file", "gopher", "ftp", "data", "javascript", "ws", "wss", "ssh", "ldap", "dict"]),
       rest=st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=40))
def test_a_non_http_scheme_is_never_allowed(scheme, rest):
    assert is_safe_webhook_destination(f"{scheme}://{rest}") is False


@settings(max_examples=300)
@given(st.ip_addresses(v=4))
def test_an_ipv4_literal_is_allowed_exactly_when_not_in_a_blocked_range(ip):
    assert is_safe_webhook_destination(f"http://{ip}/cb") is (not _blocked(ip))


@settings(max_examples=200)
@given(st.ip_addresses(v=6))
def test_an_ipv6_literal_is_allowed_exactly_when_not_in_a_blocked_range(ip):
    assert is_safe_webhook_destination(f"http://[{ip}]/cb") is (not _blocked(ip))


@settings(max_examples=100)
@given(st.sampled_from(["localhost", "LOCALHOST", "LocalHost", "metadata", "METADATA.google.internal"]),
       st.sampled_from(["http", "https"]), st.sampled_from(["", ":80", ":8080"]))
def test_the_blocked_hostnames_are_refused_in_any_case_and_port(host, scheme, port):
    assert is_safe_webhook_destination(f"{scheme}://{host}{port}/x") is False
