"""PR-SB-5.1: the leaf checker (`app/leafcheck.py`) per YANG type. Run with: pytest smo/ran-nf-oam/tests -q"""

import pytest

from app.leafcheck import check_value

PCT = {"type": "integer", "range": [[0, 100]]}
HOLES = {"type": "integer", "range": [[0, 0], [5, 10], [100, None]]}
HEX = {"type": "string", "length": [[1, 4]], "pattern": ["[A-F]+"]}
LAT = {"type": "number", "range": [[-90.0, 90.0]], "fractionDigits": 4}


@pytest.mark.parametrize("entry, value", [
    (PCT, 0), (PCT, 100), (PCT, "42"), (PCT, 7.0), (HOLES, 0), (HOLES, 7), (HOLES, 10**9),
    ({"type": "integer"}, -5), ({"type": "integer"}, "-5"),
    (LAT, 45.1234), (LAT, "-89.9999"), (LAT, 90), (LAT, 12), ({"type": "number"}, 1.5), ({"type": "number"}, "2"),
    ({"type": "boolean"}, True), ({"type": "boolean"}, "false"),
    (HEX, "ABCD"), (HEX, "F"), ({"type": "string"}, "anything"), ({"type": "string"}, 42),
    ({"type": "string", "enum": ["LOCKED", "UNLOCKED"]}, "LOCKED"), ({"type": "integer", "enum": [1, 2]}, 2),
    ({"type": "array"}, [1, 2]), ({"type": "object"}, {"a": 1}), ({"type": "any"}, object()), ({}, 3),
])
def test_an_acceptable_value_has_no_problem(entry, value):
    """Each YANG leaf type accepts the values it should (including integer-valued strings, bounds, enum members and an unconstrained type), so a valid CM write is not refused.
    """
    assert check_value(entry, value) is None


@pytest.mark.parametrize("entry, value, reason", [
    (PCT, 101, "is out of range 0..100"), (PCT, -1, "is out of range 0..100"),
    (PCT, True, "is not an integer"), (PCT, 2.5, "is not an integer"), (PCT, "ten", "is not an integer"), (PCT, None, "is not an integer"),
    (HOLES, 3, "is out of range 0 | 5..10 | 100.."), (HOLES, 11, "is out of range 0 | 5..10 | 100.."),
    (LAT, 90.0001, "is out of range -90.0..90.0"), (LAT, 1.23456, "has more than 4 fraction digits"), (LAT, "north", "is not a number"),
    (LAT, True, "is not a number"),
    ({"type": "boolean"}, "yes", "is not a boolean"), ({"type": "boolean"}, 1, "is not a boolean"),
    (HEX, "", "has length 0, allowed 1..4"), (HEX, "ABCDE", "has length 5, allowed 1..4"),
    (HEX, "abc", "does not match the pattern [A-F]+"), (HEX, "AXC", "does not match the pattern [A-F]+"),
    ({"type": "string"}, True, "is not a string"), ({"type": "string"}, ["a"], "is not a string"),
    ({"type": "string", "enum": ["LOCKED", "UNLOCKED"]}, "OPEN", "is not one of ['LOCKED', 'UNLOCKED']"),
    ({"type": "integer", "enum": [1, 2]}, 3, "is not one of [1, 2]"),
    ({"type": "array"}, "x", "is not a list"), ({"type": "object"}, [], "is not an object"),
])
def test_an_unacceptable_value_names_its_problem(entry, value, reason):
    """A value outside the type, range, length, pattern, fraction digits or enum is refused with the exact reason text that ends up in the rejection.
    """
    assert check_value(entry, value) == reason


def test_a_pattern_python_cannot_read_is_skipped_not_guessed_at():
    """A YANG pattern that Python's re cannot compile is skipped, while a readable pattern in the same entry is still enforced."""
    entry = {"type": "string", "pattern": ["\\p{L}+(", "[a-z]+"]}
    assert check_value(entry, "abc") is None
    assert check_value(entry, "ABC") == "does not match the pattern [a-z]+"


def test_every_pattern_must_match():
    """When a leaf has several patterns, a value must match all of them (YANG semantics), and the first one it fails is named."""
    entry = {"type": "string", "pattern": ["[A-F]+", ".{2}"]}
    assert check_value(entry, "AB") is None
    assert check_value(entry, "ABC") == "does not match the pattern .{2}"
