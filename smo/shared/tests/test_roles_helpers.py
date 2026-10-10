"""smo_shared.roles: the role a request carries (only what R1 vouched for) and the enrollment secret check. Found by the mutation pilot (V-2c): no test of either.

Run with: cd smo/shared && PYTHONPATH=. python -m pytest tests/test_roles_helpers.py -q
"""

import hmac
from types import SimpleNamespace

from smo_shared import roles


def _request(**headers):
    return SimpleNamespace(headers=headers)


def test_role_of_is_the_header_R1_set_or_none():
    """role_of returns the X-R1-Role value, and None when the header is absent (a call that did not come through R1)."""
    assert roles.role_of(_request(**{roles.ROLE_HEADER: "rapp"})) == "rapp"
    assert roles.role_of(_request()) is None


def test_an_empty_role_header_is_no_role():
    """An empty role header counts as no role."""
    assert roles.role_of(_request(**{roles.ROLE_HEADER: ""})) is None


def test_the_enrollment_secret_must_match_exactly():
    """The enrollment secret matches only exactly: trailing space and a different case are refused."""
    assert roles.enrollment_secret_valid("s3cret", "s3cret") is True
    assert roles.enrollment_secret_valid("s3cret ", "s3cret") is False
    assert roles.enrollment_secret_valid("S3CRET", "s3cret") is False
    assert roles.enrollment_secret_valid("other", "s3cret") is False


def test_no_secret_presented_or_expected_never_matches():
    """A missing or empty secret never matches, including when nothing is configured, so an unconfigured platform cannot be entered with nothing."""
    assert roles.enrollment_secret_valid(None, "s3cret") is False
    assert roles.enrollment_secret_valid("", "s3cret") is False
    assert roles.enrollment_secret_valid("", "") is False          # nothing configured must not let nothing in
    assert roles.enrollment_secret_valid("s3cret", "") is False


def test_the_comparison_is_constant_time(monkeypatch):
    """The secret is compared with hmac.compare_digest on bytes, so timing does not reveal a partial match."""
    calls = []
    real = hmac.compare_digest
    monkeypatch.setattr(roles.hmac, "compare_digest", lambda a, b: calls.append((a, b)) or real(a, b))
    assert roles.enrollment_secret_valid("a", "a") is True
    assert calls == [(b"a", b"a")]
