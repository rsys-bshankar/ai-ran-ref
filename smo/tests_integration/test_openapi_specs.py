"""OPEN_ITEMS.md section 2: "No persisted OpenAPI spec files anywhere —
relying entirely on FastAPI's live /docs generation rather than committed
contracts." docs/openapi/<module>.json are that live generation, frozen to
disk (scripts/generate_openapi_specs.py writes them) so a real contract
change shows up as a diff in code review, not only at runtime. This test is
what keeps them honest: it regenerates every module's schema fresh and
fails if a committed file has drifted — run generate_openapi_specs.py and
commit the result to fix a failure here.
Run with: PYTHONPATH=shared pytest tests_integration -q
"""

import json
from pathlib import Path

SMO_ROOT = Path(__file__).resolve().parent.parent
OPENAPI_DIR = SMO_ROOT / "docs" / "openapi"


def test_every_module_has_a_committed_openapi_spec(loaded_apps):
    missing = [name for name in loaded_apps if not (OPENAPI_DIR / f"{name}.json").exists()]
    assert missing == []


def test_committed_openapi_specs_match_the_live_schema(loaded_apps):
    stale = []
    for name, main_module in loaded_apps.items():
        committed = json.loads((OPENAPI_DIR / f"{name}.json").read_text())
        live = main_module.app.openapi()
        if committed != live:
            stale.append(name)
    assert stale == [], f"stale OpenAPI spec(s), re-run scripts/generate_openapi_specs.py: {stale}"
