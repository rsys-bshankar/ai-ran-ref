"""A KPI formula evaluator that cannot run anything (PR-MGT-11.2).

A formula is arithmetic over named counters: numbers, names, `+ - * / **`, unary minus, parentheses, comparisons, and the functions `min`, `max`,
`abs`, `sqrt`, `round`, `log10` and `ifelse(condition, then, else)`. It is parsed with `ast` and walked by this module; nothing is ever passed to `eval`,
`exec` or `compile`, and every node type not listed below is refused when the formula is parsed, not when it runs. So an attribute access, a call to
anything but the functions above, a subscript, a lambda, a comprehension, a string, an f-string or a walrus is a `FormulaError` before any counter is
read. Size is bounded as well (length, nodes, depth, exponent, literal size), so a formula cannot be made to run for long.

A division by zero, or a counter that has no value, makes the KPI *undefined* (`None`), not an error: a cell with no traffic has no success rate.
"""

import ast
import math
from collections.abc import Callable
from typing import cast

MAX_LENGTH = 500
MAX_NODES = 120
MAX_DEPTH = 24
MAX_EXPONENT = 64
MAX_LITERAL = 1e12


class FormulaError(ValueError):
    """The formula is not acceptable (a message safe to show the caller: it names the construct, never evaluates anything)."""


FUNCTIONS: dict[str, Callable[..., float]] = {"min": min, "max": max, "abs": abs, "sqrt": math.sqrt, "round": round, "log10": math.log10}
_BINARY = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow)
_COMPARE = (ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.Eq, ast.NotEq)


def parse(formula: str) -> ast.Expression:
    """The syntax tree of `formula`, or FormulaError. Run at definition time so a bad formula is refused when it is stored."""
    if not isinstance(formula, str) or not formula.strip():
        raise FormulaError("the formula is empty")
    if len(formula) > MAX_LENGTH:
        raise FormulaError(f"the formula is longer than {MAX_LENGTH} characters")
    try:
        tree = ast.parse(formula.strip(), mode="eval")
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        raise FormulaError("the formula is not a valid expression") from None
    nodes = 0

    def check(node: ast.AST, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_NODES:
            raise FormulaError(f"the formula has more than {MAX_NODES} parts")
        if depth > MAX_DEPTH:
            raise FormulaError(f"the formula is nested deeper than {MAX_DEPTH}")
        if isinstance(node, ast.Expression):
            check(node.body, depth + 1)
        elif isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(node.value, (int, float)) or abs(node.value) > MAX_LITERAL:
                raise FormulaError("only plain numbers up to 1e12 are allowed as constants")
        elif isinstance(node, ast.Name):
            if node.id in FUNCTIONS or node.id == "ifelse":
                raise FormulaError(f"{node.id} is a function and needs an argument list")
        elif isinstance(node, ast.BinOp) and isinstance(node.op, _BINARY):
            check(node.left, depth + 1)
            check(node.right, depth + 1)
            if isinstance(node.op, ast.Pow) and not (isinstance(node.right, ast.Constant) and abs(cast(float, node.right.value)) <= MAX_EXPONENT):
                raise FormulaError(f"an exponent must be a number no larger than {MAX_EXPONENT}")
        elif isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            check(node.operand, depth + 1)
        elif isinstance(node, ast.Compare) and all(isinstance(op, _COMPARE) for op in node.ops):
            for part in [node.left, *node.comparators]:
                check(part, depth + 1)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords and not any(isinstance(a, ast.Starred) for a in node.args):
            name = node.func.id
            if name not in FUNCTIONS and name != "ifelse":
                raise FormulaError(f"{name} is not a function a formula may call (allowed: {', '.join(sorted([*FUNCTIONS, 'ifelse']))})")
            if name == "ifelse" and len(node.args) != 3:
                raise FormulaError("ifelse takes three arguments: ifelse(condition, then, else)")
            if not node.args:
                raise FormulaError(f"{name} needs at least one argument")
            for arg in node.args:
                check(arg, depth + 1)
        else:
            raise FormulaError(f"{type(node).__name__} is not allowed in a formula")

    check(tree, 0)
    return tree


def names(formula: str) -> list[str]:
    """The counter names a formula uses, in order of appearance."""
    found: list[str] = []
    for node in sorted((n for n in ast.walk(parse(formula)) if isinstance(n, ast.Name)), key=lambda n: (n.lineno, n.col_offset)):
        if node.id not in FUNCTIONS and node.id != "ifelse" and node.id not in found:
            found.append(node.id)
    return found


def evaluate(formula: str, values: dict[str, float | None]) -> float | None:
    """The value of `formula` with the counters in `values`, or None when it is undefined (a counter without a value, a division by zero, a result
    that is not a finite number)."""
    tree = parse(formula)

    def run(node: ast.AST):
        if isinstance(node, ast.Expression):
            return run(node.body)
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            value = values.get(node.id)
            if value is None:
                raise _Undefined
            return value
        if isinstance(node, ast.UnaryOp):
            operand = run(node.operand)
            return -operand if isinstance(node.op, ast.USub) else +operand
        if isinstance(node, ast.BinOp):
            left, right = run(node.left), run(node.right)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                if right == 0:
                    raise _Undefined
                return left / right
            try:
                return left ** right
            except (OverflowError, ZeroDivisionError, ValueError):
                raise _Undefined from None
        if isinstance(node, ast.Compare):
            left = run(node.left)
            for op, comparator in zip(node.ops, node.comparators):
                right = run(comparator)
                ok = {ast.Lt: left < right, ast.LtE: left <= right, ast.Gt: left > right, ast.GtE: left >= right,
                      ast.Eq: left == right, ast.NotEq: left != right}[type(op)]
                if not ok:
                    return 0.0
                left = right
            return 1.0
        if isinstance(node, ast.Call):
            name = cast(ast.Name, node.func).id            # a call to anything but a plain name was refused by `check`
            if name == "ifelse":                                   # only the branch taken is evaluated: ifelse(x > 0, y / x, 0) is not undefined at 0
                return run(node.args[1]) if run(node.args[0]) else run(node.args[2])
            try:
                return FUNCTIONS[name](*[run(a) for a in node.args])
            except (ValueError, OverflowError, TypeError):
                raise _Undefined from None
        raise _Undefined                                           # unreachable for a parsed tree

    try:
        result = float(run(tree))
    except _Undefined:
        return None
    return result if math.isfinite(result) else None


class _Undefined(Exception):
    pass
