#!/usr/bin/env python3
"""Zips a sample rApp directory into <name>.csar — the actual package
Onboarding's `_validate_package` fetches over HTTP and opens.

    python3 smo/samples/build_csar.py                      # every sample
    python3 smo/samples/build_csar.py energy-saving-rapp   # one sample

Test suites, caches and the sample's own service tests are left out, and
every entry carries a fixed timestamp, so a rebuild of unchanged sources is
byte-identical.

See smo/DEMO_RUNBOOK.md for how to serve the resulting .csar file and
walk through the full rApp lifecycle against a running `docker compose
up` stack.
"""

import io
import sys
import zipfile
from pathlib import Path

SAMPLES_DIR = Path(__file__).resolve().parent
SAMPLES = ["hello-world-rapp", "energy-saving-rapp", "mobility-optimization-rapp"]
EXCLUDED_PARTS = {"__pycache__", "tests", ".pytest_cache"}
FIXED_TIME = (2026, 1, 1, 0, 0, 0)


def build_bytes(name: str) -> bytes:
    source = SAMPLES_DIR / name
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(source.rglob("*")):
            rel = path.relative_to(source)
            if path.is_file() and not EXCLUDED_PARTS & set(rel.parts):
                info = zipfile.ZipInfo(rel.as_posix(), FIXED_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, path.read_bytes())
    return buf.getvalue()


def build(name: str) -> Path:
    output = SAMPLES_DIR / f"{name}.csar"
    output.write_bytes(build_bytes(name))
    print(f"wrote {output.relative_to(SAMPLES_DIR.parent.parent)}")
    return output


def main() -> None:
    for name in sys.argv[1:] or SAMPLES:
        build(name)


if __name__ == "__main__":
    main()
