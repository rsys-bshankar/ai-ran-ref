import pytest


@pytest.fixture(autouse=True)
def open_enrollment(monkeypatch):
    """Most SME tests are about something else and register invokers freely; the enrollment tests (test_enrollment.py) take this away."""
    monkeypatch.setenv("SME_ALLOW_OPEN_ENROLLMENT", "true")
    monkeypatch.delenv("SMO_ENROLLMENT_SECRET", raising=False)
    monkeypatch.delenv("SMO_ENROLLMENT_SECRET_FILE", raising=False)
