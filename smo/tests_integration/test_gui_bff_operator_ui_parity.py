"""The GUI backend's vendored copy of the operator-ui matcher equals the original (PR-GUI-8, GUI-8.3).

`gui-bff/app/operator_ui.py` repeats `declared_routes`, `required_role`, `_template_regex`, `route_allowed` and their two patterns from
`shared/smo_shared/operator_ui.py`, because the BFF's image does not install `smo_shared`. A copy that drifted would let the BFF allow a route the
declaration does not, so this compares the definitions node by node and runs both on the same declarations and a large set of paths.
"""

import ast
import importlib.util
import itertools
import json
import random
from pathlib import Path

import pytest
import yaml

from smo_shared import operator_ui as shared

SMO_ROOT = Path(__file__).resolve().parents[1]
SHARED_SOURCE = SMO_ROOT / "shared" / "smo_shared" / "operator_ui.py"
BFF_SOURCE = SMO_ROOT / "gui-bff" / "app" / "operator_ui.py"
EXAMPLE_YAML = SMO_ROOT / "docs" / "schemas" / "operator-ui.energy-saving.example.yaml"
EXAMPLE_JSON = SMO_ROOT / "gui-bff" / "tests" / "operator_ui_example.json"
VENDORED = ("_STATIC", "_PARAM", "declared_routes", "required_role", "_template_regex", "route_allowed")


def _load_bff():
    spec = importlib.util.spec_from_file_location("bff_operator_ui", BFF_SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bff = _load_bff()


def _definitions(path: Path) -> dict[str, str]:
    """The top-level functions and simple assignments of a source file as {name: ast dump}, so two files can be compared definition by definition
    without regard to comments or layout.
    """
    found = {}
    for node in ast.parse(path.read_text()).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found[node.name] = ast.dump(node)
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            found[node.targets[0].id] = ast.dump(node.value)
    return found


# One row per vendored definition.
@pytest.mark.parametrize("name", VENDORED)
def test_each_vendored_definition_is_the_original_node_for_node(name):
    """Each definition the BFF vendors is, as a syntax tree, identical to the one in `smo_shared`, so a drifted copy fails and the fix is to change
    the original and copy it.
    """
    original, copy = _definitions(SHARED_SOURCE), _definitions(BFF_SOURCE)
    assert name in original and name in copy, f"{name} must exist in both files"
    assert original[name] == copy[name], f"gui-bff/app/operator_ui.py: {name} differs from shared/smo_shared/operator_ui.py: change the original first, then copy it"


def test_the_bff_example_is_the_validated_adr_example():
    """The BFF's JSON example is the validated form of the ADR's YAML example, so the BFF's own tests run on what the ADR specifies."""
    expected = shared.validate_operator_ui(yaml.safe_load(EXAMPLE_YAML.read_text())["operatorUi"])
    assert json.loads(EXAMPLE_JSON.read_text()) == json.loads(json.dumps(expected, sort_keys=True)), \
        "regenerate gui-bff/tests/operator_ui_example.json from docs/schemas/operator-ui.energy-saving.example.yaml (validated)"


def _declarations() -> list[dict]:
    """Three operator-page declarations to run both matchers on: the ADR example, a read-only one and a writable one, the last two with a table
    whose row detail has a second source.
    """
    example = shared.validate_operator_ui(yaml.safe_load(EXAMPLE_YAML.read_text())["operatorUi"])
    table = {"id": "t", "title": "T", "kind": "table", "source": {"path": "/instances/{instanceId}/cells"}, "rows": "items", "rowKey": "cellId",
             "columns": [{"path": "cellId", "label": "Cell"}],
             "rowDetail": {"blocks": [{"kind": "table", "title": "H", "source": {"path": "/c/{row.cellId}/load", "query": {"cell_id": "{row.cellId}"}},
                                        "rows": "items", "columns": [{"path": "at", "label": "At"}]}]}}
    return [example, {"version": 1, "readOnly": True, "panels": [table]}, {"version": 1, "panels": [table]}]


def _paths() -> list[str]:
    """A fixed list of interesting request paths plus 3000 generated from path pieces that include dot segments, encoded slashes, spaces, query and
    fragment characters, non-ASCII text and a template placeholder, with a fixed seed so a failure reproduces.
    """
    iid = "0b9f3f1e-4b0e-4a0c-9d6f-111111111111"
    fixed = [f"/instances/{iid}", f"/instances/{iid}/dashboard", f"/instances/{iid}/evaluate", f"/instances/{iid}/cells/C1/override", f"/instances/{iid}/cells/C-1_x.y~z/override",
             f"/instances/{iid}/decisions", "/c/C1/load", "/instances/abc/cells", "/instances/abc/decisions", "/", "", "/instances", "instances/abc/cells"]
    pieces = ["instances", iid, "cells", "C1", "override", "..", ".", "", "%2e%2e", "C%2F1", "a b", "x?y", "x#y", "dashboard", "decisions", "c", "load", "évil", "C1;x", "\\", "{instanceId}"]
    rng = random.Random(8)
    generated = ["/" + "/".join(rng.choice(pieces) for _ in range(rng.randint(1, 5))) for _ in range(3000)]
    return fixed + generated


# One row per declaration from `_declarations`.
@pytest.mark.parametrize("index", range(3))
def test_both_matchers_answer_the_same_for_every_method_and_path(index):
    """For each declaration both matchers list the same routes and give the same allow or refuse answer for every method and path, hostile ones
    included, so the BFF never allows what the declaration does not.
    """
    declaration = _declarations()[index]
    assert shared.declared_routes(declaration) == bff.declared_routes(declaration)
    for method, path in itertools.product(("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD"), _paths()):
        assert shared.route_allowed(declaration, method, path) == bff.route_allowed(declaration, method, path), (method, path)


def test_the_roles_agree():
    """Both copies require the same role for each HTTP method."""
    assert [shared.required_role(m) for m in ("GET", "POST", "PUT", "PATCH", "DELETE")] == [bff.required_role(m) for m in ("GET", "POST", "PUT", "PATCH", "DELETE")]


def test_what_the_bff_lets_through_is_always_something_the_shared_matcher_allows():
    """`decide` adds checks (the instance binding, the role, the query, the body) on top of the vendored matcher; it must never allow more."""
    iid = "0b9f3f1e-4b0e-4a0c-9d6f-111111111111"
    for declaration in _declarations():
        for method, path in itertools.product(("GET", "POST", "PUT", "PATCH", "DELETE"), _paths()):
            out = bff.decide(declaration, iid, method, path, "admin", params=[("cell_id", "C1")], payload={})
            if isinstance(out, bff.Call):
                assert shared.route_allowed(declaration, method, path), (method, path)
                assert out.path == path
