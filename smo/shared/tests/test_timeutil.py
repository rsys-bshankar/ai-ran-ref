"""smo_shared.timeutil.as_utc: a naive value is UTC, an aware one is left exactly as it is.

Run with: cd smo/shared && PYTHONPATH=. python -m pytest tests/test_timeutil.py -q
"""

import datetime

from smo_shared.timeutil import as_utc


def test_a_naive_value_is_taken_as_utc():
    """A datetime without a zone is read as UTC, keeping its clock time."""
    value = as_utc(datetime.datetime(2026, 10, 6, 12, 0, 0))
    assert value.tzinfo is datetime.UTC
    assert value.hour == 12


def test_an_aware_value_keeps_its_zone_and_its_instant():
    # found by the mutation pilot (V-2c): a version that stamped UTC on everything passed every test
    """A datetime that already has a zone is returned unchanged (same object, same offset), not re-stamped as UTC."""
    plus_two = datetime.timezone(datetime.timedelta(hours=2))
    original = datetime.datetime(2026, 10, 6, 12, 0, 0, tzinfo=plus_two)
    value = as_utc(original)
    assert value is original
    assert value.utcoffset() == datetime.timedelta(hours=2)
    assert value == datetime.datetime(2026, 10, 6, 10, 0, 0, tzinfo=datetime.UTC)
