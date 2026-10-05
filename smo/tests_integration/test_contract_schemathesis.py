"""Contract tests: every route of a module answers what its own OpenAPI says, on generated input (PR-V-3, QA-2).

schemathesis builds requests from the module's schema (`/openapi.json` of the live ASGI app, in process, on SQLite, with every other module behind the in-process mesh of `conftest.py` so a call a route makes to another module lands on it) and checks, for
each operation, that

  - no input makes the module answer 5xx (`not_a_server_error`),
  - the status code is one the schema documents (`status_code_conformance`),
  - the body matches the declared content type and schema (`content_type_conformance`, `response_schema_conformance`).

`MODULES` is the set under contract test; a module is added when its findings are fixed or waived, never before. Findings that are deliberate go in
`tests_integration/contract_waivers.json` as `"<module> <METHOD> <path>": "reason"` (the operation is left out of the run); a waiver that matches
no operation fails, like the CHECK-constraint waivers. `/ready` is left out everywhere: it answers 503 by design when a dependency (here: no real SME or Postgres) is missing, and its own tests cover it. The seed is fixed so a failure reproduces.
"""

import json
import os
from pathlib import Path

import pytest
import schemathesis
from hypothesis import HealthCheck, settings
from schemathesis import checks as st_checks

MODULES = [m for m in os.environ.get("SMO_CONTRACT_MODULES", "sme,a1-related").split(",") if m]
WAIVERS = json.loads(Path(__file__).with_name("contract_waivers.json").read_text())
CHECKS = (st_checks.not_a_server_error, st_checks.status_code_conformance, st_checks.content_type_conformance, st_checks.response_schema_conformance)


def _fixture(module: str):
    @pytest.fixture(name=f"contract_schema_{module.replace('-', '_')}")
    def schema_of(mesh, loaded_apps):  # `mesh` first: it wires the database and the other modules
        return schemathesis.openapi.from_asgi("/openapi.json", loaded_apps[module].app)

    return schema_of


def _contract_test(module: str):
    name = f"contract_schema_{module.replace('-', '_')}"

    schema = schemathesis.pytest.from_fixture(name).exclude(path="/ready")
    for key in WAIVERS:
        waived_module, method, path = key.split(" ", 2)
        if waived_module == module:
            schema = schema.exclude(method=method, path=path)

    @schema.parametrize()
    @settings(max_examples=int(os.environ.get("SMO_CONTRACT_EXAMPLES", "20")), deadline=None, derandomize=True,
              suppress_health_check=list(HealthCheck))
    def contract_test(case):
        case.call_and_validate(checks=CHECKS)

    contract_test.__name__ = f"test_{module.replace('-', '_')}_contract"
    return contract_test


for _module in MODULES:
    globals()[f"contract_schema_{_module.replace('-', '_')}"] = _fixture(_module)
    globals()[f"test_{_module.replace('-', '_')}_contract"] = _contract_test(_module)


def test_every_waiver_names_an_operation_of_a_module_under_test(loaded_apps):
    stale = []
    for key in WAIVERS:
        module, method, path = key.split(" ", 2)
        paths = loaded_apps[module].app.openapi()["paths"] if module in MODULES and module in loaded_apps else {}
        if method.lower() not in paths.get(path, {}):
            stale.append(key)
    assert not stale, f"waivers that match no operation: {stale}"
