"""Contract tests: every route of a module answers what its own OpenAPI says, on generated input (PR-V-3, QA-2).

schemathesis builds requests from the module's schema (`/openapi.json` of the live ASGI app, in process, on SQLite as the unit tests do) and checks, for
each operation, that

  - no input makes the module answer 5xx (`not_a_server_error`),
  - the status code is one the schema documents (`status_code_conformance`),
  - the body matches the declared content type and schema (`content_type_conformance`, `response_schema_conformance`).

`MODULES` is the set under contract test; a module is added when its findings are fixed or waived, never before. Findings that are deliberate go in
`tests_integration/contract_waivers.json` as `"<module> <METHOD> <path> <check>": "reason"`; a waiver that matches nothing fails, like the
CHECK-constraint waivers. The seed is fixed so a failure reproduces.
"""

import json
import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("SMO_DATABASE_URL", "sqlite://")
os.environ.setdefault("SME_ALLOW_OPEN_ENROLLMENT", "true")

import schemathesis  # noqa: E402
from hypothesis import HealthCheck, settings  # noqa: E402
from schemathesis import checks as st_checks  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from loader import load_app_module  # noqa: E402
from smo_shared.db import Base, get_session  # noqa: E402
from smo_shared.testing import make_test_engine  # noqa: E402

MODULES = [m for m in os.environ.get("SMO_CONTRACT_MODULES", "sme").split(",") if m]
WAIVERS = json.loads(Path(__file__).with_name("contract_waivers.json").read_text())
CHECKS = (st_checks.not_a_server_error, st_checks.status_code_conformance, st_checks.content_type_conformance, st_checks.response_schema_conformance)


def _schema(module: str):
    main = load_app_module(module)
    engine = make_test_engine()
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, future=True)

    def session():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    main.app.dependency_overrides[get_session] = session
    return schemathesis.openapi.from_asgi("/openapi.json", main.app)


def _contract_test(module: str):
    @_schema(module).parametrize()
    @settings(max_examples=int(os.environ.get("SMO_CONTRACT_EXAMPLES", "20")), deadline=None, derandomize=True,
              suppress_health_check=list(HealthCheck))
    def test_the_module_answers_what_its_schema_says(case):
        case.call_and_validate(checks=CHECKS)

    test_the_module_answers_what_its_schema_says.__name__ = f"test_{module.replace('-', '_')}_answers_what_its_schema_says"
    return test_the_module_answers_what_its_schema_says


for _module in MODULES:
    globals()[f"test_{_module.replace('-', '_')}_contract"] = _contract_test(_module)
