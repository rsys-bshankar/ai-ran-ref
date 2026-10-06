"""smo_shared.roles: the role a request carries (only what R1 vouched for) and the enrollment secret check. Found by the mutation pilot (V-2c): no test of either."""

import hmac
from types import SimpleNamespace

from smo_shared import roles


def _request(**headers):
    return SimpleNamespace(headers=headers)


def test_role_of_is_the_header_R1_set_or_none():
    assert roles.role_of(_request(**{roles.ROLE_HEADER: "rapp"})) == "rapp"
    assert roles.role_of(_request()) is None


def test_an_empty_role_header_is_no_role():
    assert roles.role_of(_request(**{roles.ROLE_HEADER: ""})) is None


def test_the_enrollment_secret_must_match_exactly():
    assert roles.enrollment_secret_valid("s3cret", "s3cret") is True
    assert roles.enrollment_secret_valid("s3cret ", "s3cret") is False
    assert roles.enrollment_secret_valid("S3CRET", "s3cret") is False
    assert roles.enrollment_secret_valid("other", "s3cret") is False


def test_no_secret_presented_or_expected_never_matches():
    assert roles.enrollment_secret_valid(None, "s3cret") is False
    assert roles.enrollment_secret_valid("", "s3cret") is False
    assert roles.enrollment_secret_valid("", "") is False          # nothing configured must not let nothing in
    assert roles.enrollment_secret_valid("s3cret", "") is False


def test_the_comparison_is_constant_time(monkeypatch):
    calls = []
    real = hmac.compare_digest
    monkeypatch.setattr(roles.hmac, "compare_digest", lambda a, b: calls.append((a, b)) or real(a, b))
    assert roles.enrollment_secret_valid("a", "a") is True
    assert calls == [(b"a", b"a")]
