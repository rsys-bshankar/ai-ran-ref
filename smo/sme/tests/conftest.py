"""Pytest configuration shared by every test file of the SME module: one autouse fixture, `open_enrollment`.

Run the suite from `smo/sme` with `PYTHONPATH=.:../shared python -m pytest tests -q`; the other fixtures (`client`, `db_session_factory`) live in `test_main.py`.
"""
import pytest


@pytest.fixture(autouse=True)
def open_enrollment(monkeypatch):
    """Opens invoker enrollment for every test: sets `SME_ALLOW_OPEN_ENROLLMENT=true` and removes any enrollment secret from the environment.

    Without it `POST /invoker-registrations` would answer 503 `ENROLLMENT_NOT_CONFIGURED` (SME refuses to guess a caller's kind), and most tests only need an invoker to exist. The
    tests of the enrollment rule itself (`test_enrollment.py`) switch it off again. `monkeypatch` restores the environment after each test.
    """
    monkeypatch.setenv("SME_ALLOW_OPEN_ENROLLMENT", "true")
    monkeypatch.delenv("SMO_ENROLLMENT_SECRET", raising=False)
    monkeypatch.delenv("SMO_ENROLLMENT_SECRET_FILE", raising=False)
