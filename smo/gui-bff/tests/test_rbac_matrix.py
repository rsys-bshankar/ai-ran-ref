"""The permission table, rule by rule and role by role (PR-V-13b, QA-6.2).

`test_rbac.py` lists routes by hand. This one is generated from `RULES` itself, so a rule added to the table is tested the day it is added and a rule that can never be
reached is found: for every rule a concrete request is made from its pattern (an id is `x1`, each alternative of `(a|b)` is a request of its own, the module catch-all is one request
per module), and for each of the three roles the decision must be

  - made by that rule (a request an earlier rule takes is a rule that is shadowed: it never decides, and the table says something it does not do),
  - an allow for the rule's minimum role and every role above it, a refusal for every role below it, and
  - for a rule with a query condition, no longer that rule's when the condition is not met.
"""

import pytest
from app.rbac import RANK, RULES, Role, decide


def _split_top(src: str, sep: str) -> list[str]:
    parts, depth, current, i = [], 0, "", 0
    while i < len(src):
        c = src[i]
        if c == "\\" and i + 1 < len(src):
            current += src[i:i + 2]
            i += 2
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        if c == sep and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += c
        i += 1
    parts.append(current)
    return parts


def expand(src: str) -> list[str]:
    """The concrete strings a pattern of the shapes the table uses can match: literals, `[^/]+`, `.*`, groups `( )`, alternatives `|` and an optional group `( )?`."""
    results = []
    for alternative in _split_top(src, "|"):
        strings = [""]
        i = 0
        while i < len(alternative):
            c = alternative[i]
            if c == "\\" and i + 1 < len(alternative):
                strings = [s + alternative[i + 1] for s in strings]
                i += 2
            elif alternative.startswith("[^/]+", i):
                strings = [s + "x1" for s in strings]
                i += 5
            elif alternative.startswith(".*", i):
                i += 2
            elif c == "(":
                depth, j = 1, i + 1
                while depth:
                    if alternative[j] == "\\":
                        j += 1
                    elif alternative[j] == "(":
                        depth += 1
                    elif alternative[j] == ")":
                        depth -= 1
                    j += 1
                inner = expand(alternative[i + 1:j - 1])
                optional = alternative.startswith("?", j)
                if optional:
                    inner = [""]            # the shortest form is enough, and `(/.*)?` is the only optional group
                    j += 1
                strings = [s + x for s in strings for x in inner]
                i = j
            else:
                strings = [s + c for s in strings]
                i += 1
        results += strings
    return results


def _cases():
    for index, rule in enumerate(RULES):
        source = rule.pattern.pattern.removeprefix("^").removesuffix("$")
        for path in expand(source):
            yield pytest.param(index, path, id=f"{index}:{rule.method} {path}" + (f" {rule.query_match}" if rule.query_match else ""))


def test_the_pattern_expander_makes_paths_the_patterns_match():
    for rule in RULES:
        source = rule.pattern.pattern.removeprefix("^").removesuffix("$")
        for path in expand(source):
            assert rule.pattern.match(path), f"{path!r} does not match {rule.pattern.pattern}"


@pytest.mark.parametrize("index, path", list(_cases()))
@pytest.mark.parametrize("role", list(Role))
def test_the_rule_decides_its_own_request_for_every_role(index, path, role):
    rule = RULES[index]
    query = {k: [v] for k, v in rule.query_match.items()}
    decision = decide(rule.method, path, query, role)
    assert decision.rule is rule, f"{rule.method} {path} is decided by another rule: this one is shadowed ({decision.rule and decision.rule.pattern.pattern})"
    assert decision.required_role == rule.role
    assert decision.allowed is (RANK[role] >= RANK[rule.role])


@pytest.mark.parametrize("index", [i for i, r in enumerate(RULES) if r.query_match])
def test_a_rule_with_a_query_condition_does_not_decide_without_it(index):
    rule = RULES[index]
    path = expand(rule.pattern.pattern.removeprefix("^").removesuffix("$"))[0]
    decision = decide(rule.method, path, {}, Role.ADMIN)
    assert decision.rule is not rule


def test_every_role_is_refused_what_no_rule_names():
    for role in Role:
        for method, path in [("POST", "/sme/oauth2/token"), ("DELETE", "/dme/types/x1"), ("GET", "/not-a-module/x1"), ("PATCH", "/ran-nf-oam/alarms/x1/ack-everything")]:
            decision = decide(method, path, {}, role)
            assert decision.rule is None and decision.allowed is False, f"{role} {method} {path} reached a rule"
