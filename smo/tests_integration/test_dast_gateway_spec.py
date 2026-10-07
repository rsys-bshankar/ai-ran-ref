"""The authenticated DAST scan scans each module through the gateway (scripts/dast_gateway_spec.py): its paths, under the gateway's prefix for it."""

import importlib.util
import json
from pathlib import Path

SMO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("dast_gateway_spec", SMO_ROOT / "scripts" / "dast_gateway_spec.py")
dast = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dast)


def _gateway_prefixes() -> set[str]:
    import re
    text = (SMO_ROOT / "r1-termination" / "app" / "main.py").read_text()
    block = text[text.index("ROUTES = {"):text.index("# PR-SEC-2: with SMO_MTLS")]
    return set(re.findall(r'^\s+"(/[a-z0-9-]+)": os\.environ', block, re.M))


def test_every_path_moves_under_the_prefix_and_nothing_else_changes():
    spec = {"openapi": "3.1.0", "servers": [{"url": "http://x"}], "paths": {"/health": {"get": {}}, "/a/{id}": {"get": {}}}, "components": {"schemas": {"A": {}}}}
    out = dast.through_gateway(spec, "sme/")
    assert list(out["paths"]) == ["/sme/health", "/sme/a/{id}"]
    assert out["paths"]["/sme/a/{id}"] is spec["paths"]["/a/{id}"] and out["components"] == spec["components"] and "servers" not in out


def test_the_modules_the_workflow_scans_are_routed_by_the_gateway_and_have_a_document():
    workflow = (SMO_ROOT.parent / ".github" / "workflows" / "smo-dast.yml").read_text()
    line = next(l for l in workflow.splitlines() if l.strip().startswith("MODULES:"))
    modules = line.split("MODULES:")[1].strip().strip('"').split()
    assert modules
    for module in modules:
        assert f"/{module}" in _gateway_prefixes(), f"the gateway has no route /{module}"
        assert json.loads((SMO_ROOT / "docs" / "openapi" / f"{module}.json").read_text())["paths"], module
