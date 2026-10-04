"""PR-MGT-11.2: the formula evaluator: arithmetic, undefined results, and hostile input."""

import pytest

from app.kpi_formula import FormulaError, evaluate, names, parse


@pytest.mark.parametrize("formula,values,expected", [
    ("a + b * 2", {"a": 1, "b": 3}, 7.0),
    ("(a - b) / b * 100", {"a": 90, "b": 100}, -10.0),
    ("100 * success / attempts", {"success": 97, "attempts": 100}, 97.0),
    ("-a + +b", {"a": 2, "b": 5}, 3.0),
    ("a ** 2", {"a": 3}, 9.0),
    ("max(a, b, 3)", {"a": 1, "b": 2}, 3.0),
    ("min(a, b)", {"a": 1, "b": 2}, 1.0),
    ("abs(a)", {"a": -4}, 4.0),
    ("sqrt(a)", {"a": 16}, 4.0),
    ("round(a)", {"a": 2.6}, 3.0),
    ("log10(a)", {"a": 1000}, 3.0),
    ("a > 1", {"a": 2}, 1.0),
    ("0 < a <= 5", {"a": 5}, 1.0),
    ("ifelse(a > 0, b / a, 0)", {"a": 0, "b": 5}, 0.0),                    # the branch not taken is not evaluated: no division by zero
    ("ifelse(a > 0, b / a, 0)", {"a": 2, "b": 5}, 2.5),
])
def test_arithmetic_comparisons_and_functions(formula, values, expected):
    assert evaluate(formula, values) == expected


@pytest.mark.parametrize("formula,values", [
    ("a / b", {"a": 1, "b": 0}),                                        # no traffic: the KPI is undefined, not an error
    ("a + b", {"a": 1}),                                                # a counter with no value
    ("a + b", {"a": 1, "b": None}),
    ("sqrt(a)", {"a": -1}),
    ("log10(a)", {"a": 0}),
    ("a ** 64 * a ** 64 * a ** 64 * a ** 64 * a ** 64 * a ** 64", {"a": 1e300}),   # overflows to infinity: not a number
])
def test_an_undefined_result_is_none_not_an_error(formula, values):
    assert evaluate(formula, values) is None


def test_the_counter_names_of_a_formula():
    assert names("100 * (ok + retry) / max(tot, 1) + ok") == ["ok", "retry", "tot"]


HOSTILE = [
    "__import__('os').system('id')",
    "().__class__.__bases__[0].__subclasses__()",
    "a.__class__",
    "open('/etc/passwd')",
    "eval('1+1')",
    "exec('x=1')",
    "compile('1','','eval')",
    "getattr(a, 'b')",
    "(lambda: 1)()",
    "[x for x in range(10)]",
    "{1: 2}",
    "a[0]",
    "'abc'",
    "f'{a}'",
    "a if b else c",
    "(x := 1)",
    "a and b",
    "not a",
    "a % 2",
    "a // 2",
    "a ** b",                                                           # an exponent must be a plain number
    "2 ** 100000",
    "2 ** 65",
    "9" * 30,
    "1e999",
    "True",
    "None",
    "min()",
    "max(a, key=len)",
    "min(*a)",
    "ifelse(a)",
    "ifelse",
    "if(a, b, c)",
    "min",
    "a;b",
    "import os",
    "a\nb",
    "",
    "   ",
]


@pytest.mark.parametrize("formula", HOSTILE)
def test_hostile_or_malformed_input_is_refused_when_parsed_and_never_run(formula):
    with pytest.raises(FormulaError):
        parse(formula)


def test_size_limits():
    with pytest.raises(FormulaError):
        parse("a + " * 200 + "a")                                       # too many parts
    with pytest.raises(FormulaError):
        parse("-" * 40 + "a")                                          # too deep
    with pytest.raises(FormulaError):
        parse("a" * 501)


def test_a_refused_formula_never_reaches_a_counter_or_a_side_effect(monkeypatch):
    called = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: called.append(a))
    with pytest.raises(FormulaError):
        evaluate("print('x')", {})
    assert called == []
