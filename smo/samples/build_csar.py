#!/usr/bin/env python3
"""Zips hello-world-rapp/ into hello-world-rapp.csar — the actual
package Onboarding's `_validate_package` fetches over HTTP and opens.

See smo/DEMO_RUNBOOK.md for how to serve the resulting .csar file and
walk through the full rApp lifecycle against a running `docker compose
up` stack.
"""

import zipfile
from pathlib import Path

SAMPLES_DIR = Path(__file__).resolve().parent
SOURCE_DIR = SAMPLES_DIR / "hello-world-rapp"
OUTPUT_PATH = SAMPLES_DIR / "hello-world-rapp.csar"


def main() -> None:
    with zipfile.ZipFile(OUTPUT_PATH, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(SOURCE_DIR.rglob("*")):
            if path.is_file():
                z.write(path, path.relative_to(SOURCE_DIR))
    print(f"wrote {OUTPUT_PATH.relative_to(SAMPLES_DIR.parent.parent)}")


if __name__ == "__main__":
    main()
