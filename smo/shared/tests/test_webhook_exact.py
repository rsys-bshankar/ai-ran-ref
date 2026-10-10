"""The exact edges of the address helpers of the SSRF guard in `smo_shared/webhook.py` (`_parse_inet_aton`, `_literal_ip`, `_is_blocked_address`,
`_resolved_addresses`, `_resolves_to_blocked_address`). They are in the mutation scope of the shared library (`scripts/mutation_pilot.sh`), so each assertion
pins an exact returned address, an exact exception message, an exact blocked / allowed verdict or an exact resolver call, and names the change it would catch.

Run with: cd smo/shared && PYTHONPATH=. python -m pytest tests/test_webhook_exact.py -q

The resolver tests replace `webhook.socket.getaddrinfo` with a recording stub, so no test here touches the network.
"""

import ipaddress
import socket

import pytest

from smo_shared import webhook
from smo_shared.webhook import (_is_blocked_address, _literal_ip, _parse_inet_aton, _resolved_addresses, _resolves_to_blocked_address)

V4 = ipaddress.IPv4Address
V6 = ipaddress.IPv6Address


# each row: a spelling and the address inet_aton reads from it; the rows cover each part count, each base prefix and the largest value of each part count
@pytest.mark.parametrize("text,expected", [
    ("0", "0.0.0.0"), ("1", "0.0.0.1"), ("010", "0.0.0.8"), ("0x10", "0.0.0.16"), ("0X10", "0.0.0.16"), ("0x", "0.0.0.0"), ("00", "0.0.0.0"),
    ("4294967295", "255.255.255.255"), ("2130706433", "127.0.0.1"),
    ("127.1", "127.0.0.1"), ("1.16777215", "1.255.255.255"), ("0x7f.1", "127.0.0.1"),
    ("1.2.65535", "1.2.255.255"), ("1.2.3", "1.2.0.3"),
    ("1.2.3.4", "1.2.3.4"), ("1.2.3.255", "1.2.3.255"), ("255.0.0.1", "255.0.0.1"), ("0177.0.0.1", "127.0.0.1"), ("0x7f.0.0.1", "127.0.0.1"),
])
def test_inet_aton_reads_each_spelling_to_its_exact_address(text, expected):
    """Every part count, base prefix and largest value of a part count gives exactly the address `inet_aton` gives (a changed bound or base shifts it)."""
    assert _parse_inet_aton(text) == V4(expected)


# each row: a numeric host that is not an address and the exact reason given
@pytest.mark.parametrize("text,message", [
    ("4294967296", "numeric label out of range"),         # one past the one-number maximum
    ("1.16777216", "numeric label out of range"),         # one past the two-number maximum
    ("1.2.65536", "numeric label out of range"),          # one past the three-number maximum
    ("1.2.3.256", "numeric label out of range"),          # one past the four-number maximum
    ("256.0.0.1", "numeric label out of range"),          # a leading part over 255 with a valid last part
    ("256.1", "numeric label out of range"),
    ("1.256.0.1", "numeric label out of range"),
    ("08", "bad numeric label"),                          # octal with an 8
    ("1.09", "bad numeric label"),
    ("1.2.3.4.5", "too many numeric labels"),
])
def test_inet_aton_rejects_with_the_exact_reason(text, message):
    """Numeric text that is not an address raises ValueError with its specific message (the caller blocks it; the message is what a log reader sees)."""
    with pytest.raises(ValueError, match=f"^{message}$"):
        _parse_inet_aton(text)


def test_inet_aton_a_leading_part_of_255_is_in_range():
    """255 is the largest leading part: `255.255.255.255` is an address, so the bound is not 'over 254'."""
    assert _parse_inet_aton("255.255.255.255") == V4("255.255.255.255")
    assert _parse_inet_aton("255.1") == V4("255.0.0.1")


# each row: text that is not numeric labels only, so it is a host name (None), not an error
@pytest.mark.parametrize("text", ["", "example.com", "1.2.3.x", "0xg", "a", "-1"])
def test_inet_aton_returns_none_for_a_name(text):
    """A host name is None, not an error and not an address."""
    assert _parse_inet_aton(text) is None


def test_literal_ip_drops_an_ipv6_zone_at_the_first_percent():
    """`fe80::1%eth0` is `fe80::1` (no scope id kept); a second percent sign is part of the dropped zone, not a different address."""
    assert _literal_ip("fe80::1%eth0") == V6("fe80::1")
    assert _literal_ip("fe80::1%eth0").scope_id is None
    assert _literal_ip("fe80::1%a%b") == V6("fe80::1")


def test_literal_ip_not_an_ipv6_address_has_its_exact_message():
    """Text with a colon that is not an IPv6 address raises ValueError('not an IPv6 address')."""
    with pytest.raises(ValueError, match=r"^not an IPv6 address$"):
        _literal_ip("1:2:3")
    with pytest.raises(ValueError, match=r"^not an IPv6 address$"):
        _literal_ip("host:80")


def test_literal_ip_routes_dotted_text_to_the_inet_aton_reading():
    """Without a colon the host is read as `inet_aton` text: a short spelling, a name and an out-of-range number each take their branch."""
    assert _literal_ip("127.1") == V4("127.0.0.1")
    assert _literal_ip("example.com") is None
    with pytest.raises(ValueError):
        _literal_ip("256.1")


# each row: an IPv6 address and whether it is blocked; the NAT64 rows are the ones the embedded-address rule decides
@pytest.mark.parametrize("text,blocked", [
    ("64:ff9b::7f00:1", True),        # NAT64 around 127.0.0.1
    ("64:ff9b::a9fe:a9fe", True),     # NAT64 around 169.254.169.254
    ("64:ff9b::808:808", True),       # NAT64 around 8.8.8.8: the translation prefix is blocked whole
    ("64:ff9b::a00:1", True),         # NAT64 around 10.0.0.1
    ("::ffff:127.0.0.1", True), ("::ffff:8.8.8.8", False),
    ("2002:7f00:1::", True), ("2002:808:808::", False),
    ("::1", True), ("fe80::1", True), ("ff02::1", True), ("::", True), ("2001:4860:4860::8888", False),
])
def test_is_blocked_address_ipv6_verdicts(text, blocked):
    """The verdict for an IPv6 address, including the IPv4 address inside a NAT64, mapped or 6to4 one, is exact (blocked inner address blocks it, public one does not)."""
    assert _is_blocked_address(V6(text)) is blocked


def test_is_blocked_address_nat64_prefix_is_blocked_whole():
    """Every address of 64:ff9b::/96 is blocked, the first and last included, whatever IPv4 address it wraps (the rule must not depend on the wrapped address)."""
    assert _is_blocked_address(V6("64:ff9b::")) is True
    assert _is_blocked_address(V6("64:ff9b::ffff:ffff")) is True
    assert _is_blocked_address(V6("64:ff9b::808:808")) is True


@pytest.fixture
def lookups(monkeypatch):
    """Replace `webhook.socket.getaddrinfo` with a stub that records each call as (args, kwargs) and answers with `answer` (a list of infos, or an exception to raise);
    the test sets `lookups.answer`."""
    class Stub:
        """The recording resolver stub: `calls` is the list of (args, kwargs), `answer` what the next call returns or raises."""
        def __init__(self):
            self.calls = []
            self.answer = []

        def __call__(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            if isinstance(self.answer, BaseException):
                raise self.answer
            return self.answer
    stub = Stub()
    monkeypatch.setattr(webhook.socket, "getaddrinfo", stub)
    return stub


def _info(address):
    """One getaddrinfo row for `address` (family and the sockaddr tuple; only the address is read)."""
    return (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 0))


def test_resolved_addresses_asks_the_resolver_for_the_host_port_and_stream_type(lookups):
    """The resolver is called with the host, the port the URL carries and `type=SOCK_STREAM`, nothing else (the port selects the sockaddr, the type avoids duplicates)."""
    lookups.answer = [_info("8.8.8.8")]
    assert _resolved_addresses("example.com", 8443) == [V4("8.8.8.8")]
    assert lookups.calls == [(("example.com", 8443), {"type": socket.SOCK_STREAM})]
    _resolved_addresses("example.com", None)
    assert lookups.calls[1] == (("example.com", None), {"type": socket.SOCK_STREAM})


def test_resolved_addresses_drops_a_zone_at_the_first_percent(lookups):
    """An address with a scope (`fe80::1%eth0`) is the address without it, with no scope id kept, however many percent signs follow."""
    lookups.answer = [(socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::1%eth0", 0, 0, 2)), (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("fe80::2%a%b", 0, 0, 2))]
    found = _resolved_addresses("h", None)
    assert found == [V6("fe80::1"), V6("fe80::2")]
    assert all(ip.scope_id is None for ip in found)


def test_resolved_addresses_skips_an_unreadable_entry_and_keeps_the_rest(lookups):
    """An entry that is not an IP address is skipped, and the entries after it are still read (a stop at the first bad one would hide a blocked address)."""
    lookups.answer = [_info("not-an-ip"), _info("127.0.0.1"), _info("also bad"), _info("8.8.8.8")]
    assert _resolved_addresses("h", 80) == [V4("127.0.0.1"), V4("8.8.8.8")]


@pytest.mark.parametrize("error", [socket.gaierror("no such name"), UnicodeError("bad label"), OSError("resolver down")])
def test_resolved_addresses_is_empty_when_the_resolver_fails(lookups, error):
    """A name that does not resolve (or a resolver failure) gives an empty list, not an exception: the call then fails by itself."""
    lookups.answer = error
    assert _resolved_addresses("h", 80) == []


def test_resolves_to_blocked_address_passes_the_port_to_the_resolver(lookups):
    """The URL's port reaches the lookup, and a literal-free name with a blocked record is blocked."""
    lookups.answer = [_info("127.0.0.1")]
    assert _resolves_to_blocked_address("http://example.com:8443/cb") is True
    assert lookups.calls == [(("example.com", 8443), {"type": socket.SOCK_STREAM})]
    assert _resolves_to_blocked_address("http://example.com/cb") is True
    assert lookups.calls[1][0] == ("example.com", None)


def test_resolves_to_blocked_address_every_record_counts(lookups):
    """One public and one loopback record is blocked whichever comes first; only all-public records pass; an unresolvable name is not blocked here."""
    lookups.answer = [_info("8.8.8.8"), _info("127.0.0.1")]
    assert _resolves_to_blocked_address("http://example.com/") is True
    lookups.answer = [_info("127.0.0.1"), _info("8.8.8.8")]
    assert _resolves_to_blocked_address("http://example.com/") is True
    lookups.answer = [_info("8.8.8.8"), _info("10.0.0.5")]
    assert _resolves_to_blocked_address("http://example.com/") is False
    lookups.answer = socket.gaierror("nope")
    assert _resolves_to_blocked_address("http://example.com/") is False


@pytest.mark.parametrize("destination", [None, "", "http://", "http:///path", "relative/path", "http://[::1", "http://example.com:99999/", "http://example.com:x/",
                                         "http://256.1/", "http://08/", "http://1.2.3.4.5/", "http://[1:2:3]/"])
def test_resolves_to_blocked_address_is_blocked_for_what_has_no_usable_host(lookups, destination):
    """No destination, no host, an unparsable URL or port, or numeric text that is not an address is blocked, and no lookup is made for it."""
    assert _resolves_to_blocked_address(destination) is True
    assert lookups.calls == []


@pytest.mark.parametrize("destination", ["http://8.8.8.8/", "http://127.0.0.1/", "http://127.1:8080/", "http://[::1]/", "http://2130706433/", "http://[2001:db8::1]:80/"])
def test_resolves_to_blocked_address_needs_no_lookup_for_a_literal_ip(lookups, destination):
    """A literal IP (blocked or not: the literal check judges it) is answered False without asking the resolver."""
    lookups.answer = [_info("127.0.0.1")]
    assert _resolves_to_blocked_address(destination) is False
    assert lookups.calls == []


# each row: a host as written and the name a resolver sees (lower case, folded dots, no trailing dots, and nothing else stripped)
@pytest.mark.parametrize("text,expected", [("LocalHost.", "localhost"), ("a.b..", "a.b"), ("ExampleX.", "examplex"), ("HOSTX", "hostx"), ("x", "x"), ("a。b．", "a.b"),
                                           ("１２７.0.0.1", "127.0.0.1"), ("", "")])
def test_canonical_host_lowers_folds_and_strips_only_trailing_dots(text, expected):
    """Only dots are stripped from the end (a name ending in the letter x keeps it) and the result is lower case."""
    assert webhook._canonical_host(text) == expected
