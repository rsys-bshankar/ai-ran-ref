#!/usr/bin/env python3
"""Regenerates smo/docs/openapi/<module>.json for every module from its own
live FastAPI app.openapi() output.

OPEN_ITEMS.md section 2: "No persisted OpenAPI spec files anywhere — relying
entirely on FastAPI's live /docs generation rather than committed contracts."
These files ARE that live generation, just frozen to disk so a contract
change shows up as a diff in review rather than only at runtime.
`tests_integration/test_openapi_specs.py` fails CI if a committed file drifts
from what the app would generate today — run this script and commit the
result to fix that.
"""

import json
import sys
from pathlib import Path

SMO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SMO_ROOT / "tests_integration"))

from loader import load_app_module  # noqa: E402

ALL_MODULES = [
    "r1-termination", "sme", "dme", "onboarding", "rapp-mgmt", "ran-nf-oam",
    "a1-related", "nfo", "focom", "ai-ml-workflow", "ran-analytics",
    "intent-service", "so-smos", "sa-smos", "mock-near-rt-ric", "mock-o1-adaptor",
]


def generate(module_dir: str) -> dict:
    main = load_app_module(module_dir)
    return main.app.openapi()


def main() -> None:
    out_dir = SMO_ROOT / "docs" / "openapi"
    out_dir.mkdir(parents=True, exist_ok=True)
    for module_dir in ALL_MODULES:
        spec = generate(module_dir)
        out_path = out_dir / f"{module_dir}.json"
        out_path.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n")
        print(f"wrote {out_path.relative_to(SMO_ROOT)}")


if __name__ == "__main__":
    main()
