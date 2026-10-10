#!/usr/bin/python3
"""Fuzz target: Onboarding's CSAR parsers, fed an attacker-shaped package.

A package location is caller-supplied, so its bytes are untrusted. This builds
a zip from the fuzz input and runs the same parsers `_validate_package` runs on
a downloaded package. Only the failures onboarding documents
(ONBOARD_VALIDATION_FAILURES: a bad zip, a missing entry, malformed YAML/JSON,
an invalid runtime profile) are acceptable; anything else is a crash, because
in the service it would escape the FAILED path. Run by ClusterFuzzLite
(.clusterfuzzlite/); locally: PYTHONPATH=shared:onboarding python3
fuzz/fuzz_csar_parsers.py -max_total_time=30
"""
import os
import sys
import warnings
import zipfile
from io import BytesIO

import atheris

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "onboarding"))
# Importing the app builds the database engine, which has no default URL (PR-DB-1); the parsers never connect.
os.environ.setdefault("SMO_DATABASE_URL", "sqlite://")

with atheris.instrument_imports():
    from app.main import (ONBOARD_VALIDATION_FAILURES, _asd_identity, _parse_ai_capabilities,
                          _parse_sme_declarations)

warnings.simplefilter("ignore")  # zipfile's "Duplicate name" warning

# the entries the real parsers look for, so the fuzzer reaches their code quickly
KNOWN_NAMES = [
    "manifest.yaml", "capabilities.yaml", "TOSCA-Metadata/TOSCA.meta", "Definitions/asd.yaml",
    "Files/Sme/providers/provider.json", "Files/Sme/serviceapis/api.json", "Artifacts/chart.tgz",
]


def TestOneInput(data: bytes) -> None:
    """One fuzz iteration: builds a zip of up to six entries from `data` and runs Onboarding's three CSAR parsers on it.

    Entry names are often one of KNOWN_NAMES so the parsers' code is reached. A failure Onboarding documents
    (ONBOARD_VALIDATION_FAILURES) is the expected outcome for a bad package and is swallowed; any other exception propagates
    and atheris reports it, because in the service it would escape the FAILED path.
    """
    fdp = atheris.FuzzedDataProvider(data)
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for _ in range(fdp.ConsumeIntInRange(0, 6)):
            name = fdp.PickValueInList(KNOWN_NAMES) if fdp.ConsumeBool() else fdp.ConsumeUnicodeNoSurrogates(24)
            name = name.split("\x00")[0] or "x"  # zipfile truncates at NUL and cannot write an empty name (harness limits, not findings)
            z.writestr(name, fdp.ConsumeBytes(fdp.ConsumeIntInRange(0, 400)))
    buf.seek(0)
    try:
        with zipfile.ZipFile(buf) as z:
            _parse_ai_capabilities(z)
            _parse_sme_declarations(z)
            if "Definitions/asd.yaml" in z.namelist():
                _asd_identity(z.read("Definitions/asd.yaml").decode(errors="replace"))
    except ONBOARD_VALIDATION_FAILURES:
        pass


def main() -> None:
    """Hands `TestOneInput` to atheris, which parses the fuzzer's command-line flags (such as -max_total_time) and runs until a finding or the limit."""
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
