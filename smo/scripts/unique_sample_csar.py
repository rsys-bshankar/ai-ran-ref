#!/usr/bin/env python3
"""Write a signed copy of a sample package that no stack has onboarded yet: `unique_sample_csar.py NAME OUT.csar`.

Onboarding refuses a package whose bytes it already holds, so the conformance pack's runtime checks (`conformance/rapp`) cannot onboard a committed sample on a
stack that has already onboarded it (the demo runbook replay does, in CI). This adds one file with a random marker to the sample's sources and signs the result with
the demo publisher's key, so it verifies like the committed one and is new to the stack. Not for anything but a test stack.
"""

import os
import sys
import zipfile
from pathlib import Path

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
sys.path.insert(0, str(SAMPLES))

import build_csar  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.splitlines()[0], file=sys.stderr)
        return 2
    name, out = argv
    marker = f"conformance run {os.urandom(8).hex()}\n"
    files = build_csar.source_files(name)
    files["conformance-run.txt"] = marker.encode()
    files |= build_csar.csar_signing.signing_entries(files, build_csar.csar_signing.load_private_key(build_csar.DEMO_KEY.read_bytes()))
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for rel, content in files.items():
            info = zipfile.ZipInfo(rel, build_csar.FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, content)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
