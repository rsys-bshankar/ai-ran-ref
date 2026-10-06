#!/usr/bin/env python3
"""Zip this sample into tx-muting-rapp.csar, the package Onboarding fetches over HTTP and validates.

    python3 scripts/build_csar.py            # writes tx-muting-rapp.csar next to README.md
    python3 scripts/build_csar.py --check    # exit 1 if the committed .csar differs from the sources

Same format as smo/samples/build_csar.py (fixed entry timestamps, sorted entries, so unchanged sources rebuild
byte-identically), restricted to what belongs in the package: the descriptor files, `app/` and `demo.py`. Tests,
documentation, the O1 adaptor simulator, the compose overlay, scripts and caches stay out.
"""

import io
import sys
import zipfile
from pathlib import Path

SAMPLE = Path(__file__).resolve().parents[1]
OUTPUT = SAMPLE / "tx-muting-rapp.csar"
INCLUDE = ("TOSCA-Metadata", "Definitions", "app", "manifest.yaml", "capabilities.yaml", "demo.py")
FIXED_TIME = (2026, 1, 1, 0, 0, 0)


def build_bytes() -> bytes:
    files = []
    for entry in INCLUDE:
        path = SAMPLE / entry
        files += [p for p in sorted(path.rglob("*")) if p.is_file()] if path.is_dir() else [path]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for path in sorted(f for f in files if "__pycache__" not in f.parts):
            info = zipfile.ZipInfo(path.relative_to(SAMPLE).as_posix(), FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, path.read_bytes())
    return buf.getvalue()


def main() -> int:
    data = build_bytes()
    if sys.argv[1:] == ["--check"]:
        if not OUTPUT.exists() or OUTPUT.read_bytes() != data:
            print(f"{OUTPUT.name} is stale: run scripts/build_csar.py", file=sys.stderr)
            return 1
        print(f"{OUTPUT.name} is up to date")
        return 0
    OUTPUT.write_bytes(data)
    print(f"wrote {OUTPUT.name} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
