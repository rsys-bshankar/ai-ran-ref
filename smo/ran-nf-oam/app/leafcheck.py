"""Leaf type and constraint checker driven by a CM descriptor entry (PR-SB-5.1).

A descriptor attribute entry (`scripts/ingest_yang_schema.py`, `ingest_cm_schema.py`) is

    {"type": "integer" | "number" | "boolean" | "string" | "array" | "object" | "any",
     "enum"?: [...], "range"?: [[lo, hi], ...], "length"?: [[lo, hi], ...], "pattern"?: [regex, ...],
     "fractionDigits"?: n}

`check_value(entry, value)` answers `None` when the value is acceptable and otherwise a reason to put after
`attr=value`, so a CM write can be refused before anything is dispatched (`vendors.schema_problems`). It is
deliberately a *leaf* checker: it knows nothing of `must`, `when`, `leafref` targets or lists' keys.

* integer: a JSON integer, or an integer-valued string (RFC 7951 carries 64-bit integers as strings) or float;
  never a boolean. Then `range` (an interval list: the leaf's own restriction, or its type's native bounds).
* number (decimal64): a JSON number or numeric string, at most `fractionDigits` decimals, within `range`.
* boolean: `true`/`false` or the strings "true"/"false".
* enum (any type): the value must be one of the listed members.
* string: a string (a number is accepted and read as its text, because the descriptor folds `union`,
  `leafref`, `identityref` and `bits` into "string"; a boolean, list or object is not), then `length`
  (characters) and every `pattern` (YANG patterns are anchored; a pattern Python's `re` cannot read is skipped
  rather than guessed at).
* array / object: a list / a dict. any: anything.

An interval bound of None is unbounded.
"""

import re
from decimal import Decimal, InvalidOperation
from typing import cast

_INTEGER_TEXT = re.compile(r"^[+-]?\d+$")


def _intervals_text(intervals) -> str:
    def bound(b):
        return "" if b is None else str(b)
    return " | ".join(f"{bound(lo)}..{bound(hi)}" if lo != hi else bound(lo) for lo, hi in intervals)


def _within(value, intervals) -> bool:
    for lo, hi in intervals:
        if (lo is None or value >= Decimal(str(lo))) and (hi is None or value <= Decimal(str(hi))):
            return True
    return False


def _as_integer(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str) and _INTEGER_TEXT.match(value.strip()):
        return int(value.strip())
    return None


def _as_decimal(value) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def check_value(entry: dict, value) -> str | None:
    kind = entry.get("type", "any")
    if entry.get("enum"):  # an enumeration is its list, whatever type its members have
        return None if value in entry["enum"] else f"is not one of {entry['enum']}"
    if kind == "integer":
        number = _as_integer(value)
        if number is None:
            return "is not an integer"
        if entry.get("range") and not _within(Decimal(number), entry["range"]):
            return f"is out of range {_intervals_text(entry['range'])}"
        return None
    if kind == "number":
        decimal = _as_decimal(value)
        if decimal is None:
            return "is not a number"
        digits = entry.get("fractionDigits")
        if digits is not None and -cast(int, decimal.as_tuple().exponent) > digits and decimal != decimal.quantize(Decimal(1).scaleb(-digits)):
            return f"has more than {digits} fraction digits"
        if entry.get("range") and not _within(decimal, entry["range"]):
            return f"is out of range {_intervals_text(entry['range'])}"
        return None
    if kind == "boolean":
        if isinstance(value, bool) or value in ("true", "false"):
            return None
        return "is not a boolean"
    if kind == "string":
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            return "is not a string"
        text = value if isinstance(value, str) else str(value)
        if entry.get("length") and not _within(Decimal(len(text)), entry["length"]):
            return f"has length {len(text)}, allowed {_intervals_text(entry['length'])}"
        for pattern in entry.get("pattern") or []:
            try:
                matched = re.fullmatch(pattern, text) is not None
            except re.error:
                continue
            if not matched:
                return f"does not match the pattern {pattern}"
        return None
    if kind == "array":
        return None if isinstance(value, list) else "is not a list"
    if kind == "object":
        return None if isinstance(value, dict) else "is not an object"
    return None
