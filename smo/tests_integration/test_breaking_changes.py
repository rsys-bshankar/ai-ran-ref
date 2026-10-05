"""The breaking-change checker (scripts/check_breaking_changes.py, PR-V-4) finds each kind of break it names, and nothing for a compatible change."""

import copy
import importlib.util
import json
from pathlib import Path

SMO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_breaking_changes", SMO / "scripts" / "check_breaking_changes.py")
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)

BASE = {
    "paths": {
        "/things": {
            "get": {"parameters": [{"name": "limit", "in": "query", "required": False, "schema": {"type": "integer", "maximum": 500}},
                                   {"name": "kind", "in": "query", "schema": {"type": "string", "enum": ["a", "b"]}}],
                    "responses": {"200": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/Page"}}}}}},
            "post": {"requestBody": {"required": False, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Thing"}}}},
                     "responses": {"201": {"description": "created"}}},
        },
        "/gone": {"delete": {"responses": {"204": {"description": "ok"}}}},
    },
    "components": {"schemas": {
        "Thing": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}, "size": {"type": "integer"}}},
        "Page": {"type": "object", "required": ["items", "total"], "properties": {"items": {"type": "array", "items": {"type": "string"}}, "total": {"type": "integer"}}},
    }},
}


def rules(new):
    return sorted((r, w) for r, _, w, _ in checker.compare_specs("m", BASE, new))


def test_an_identical_spec_has_no_break():
    assert rules(copy.deepcopy(BASE)) == []


def test_compatible_changes_are_not_breaks():
    new = copy.deepcopy(BASE)
    new["paths"]["/new"] = {"get": {"responses": {"200": {"description": "ok"}}}}
    new["paths"]["/things"]["get"]["parameters"].append({"name": "extra", "in": "query", "required": False, "schema": {"type": "string"}})
    new["paths"]["/things"]["get"]["parameters"][1]["schema"]["enum"] = ["a", "b", "c"]  # widened
    new["paths"]["/things"]["get"]["parameters"][0]["schema"]["maximum"] = 1000          # widened
    new["paths"]["/things"]["get"]["responses"]["404"] = {"description": "now documented"}
    new["components"]["schemas"]["Thing"]["properties"]["extra"] = {"type": "string"}
    new["components"]["schemas"]["Page"]["properties"]["more"] = {"type": "boolean"}
    assert rules(new) == []


def test_each_kind_of_break_is_found():
    new = copy.deepcopy(BASE)
    del new["paths"]["/gone"]
    get = new["paths"]["/things"]["get"]
    get["parameters"].append({"name": "must", "in": "query", "required": True, "schema": {"type": "string"}})
    get["parameters"][0]["schema"]["maximum"] = 100
    get["parameters"][1]["schema"]["enum"] = ["a"]
    new["components"]["schemas"]["Thing"]["required"] = ["name", "size"]
    new["components"]["schemas"]["Thing"]["properties"]["name"]["type"] = "integer"
    new["components"]["schemas"]["Page"]["properties"].pop("total")
    del new["paths"]["/things"]["post"]["responses"]["201"]
    new["paths"]["/things"]["post"]["requestBody"]["required"] = True
    found = rules(new)
    assert ("removed-operation", "") in found
    assert ("new-required-parameter", "query must") in found
    assert ("narrowed-parameter", "query limit") in found
    assert ("narrowed-parameter", "query kind") in found
    assert ("new-required-property", "body.size") in found
    assert ("changed-type", "body.name") in found
    assert ("removed-property", "response 200.total") in found
    assert ("removed-response", "201") in found
    assert ("new-required-parameter", "body") in found


def test_the_committed_waivers_are_well_formed():
    waivers = json.loads((SMO / "scripts" / "breaking_change_waivers.json").read_text())
    assert all(isinstance(reason, str) and len(reason) > 20 for reason in waivers.values())
