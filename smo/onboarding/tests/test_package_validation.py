"""Validation of package bytes (`app/package_validation.py`), which needs neither the database nor the web framework and which the offline conformance validator also runs.

The helper `csar` builds a small CSAR zip. Run: `cd smo/onboarding && PYTHONPATH=.:../shared python -m pytest tests/test_package_validation.py -q`.
"""

import io
import zipfile

import pytest

from app.package_validation import PackageValidationFailed, validate_package_bytes


def csar(**overrides) -> bytes:
    """Builds CSAR bytes with a TOSCA.meta and an ASD, plus `overrides` (keyword names use `__` for `/`; a value of None omits the entry)."""
    files = {"TOSCA-Metadata/TOSCA.meta": "Entry-Definitions: Definitions/main.yaml\n", "Definitions/main.yaml": "application_name: X\napplication_version: 1.0.0\nprovider: P\n"}
    files.update({k.replace("__", "/"): v for k, v in overrides.items()})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            if content is not None:
                z.writestr(name, content)
    return buf.getvalue()


def test_a_well_formed_package_gives_the_entry_artifacts_hash_and_identity():
    """A good package returns its entry definitions, the registered artifacts with their access URLs, a 64-character hash and the ASD identity."""
    entry, artifacts, integrity_hash, identity = validate_package_bytes(csar(Artifacts__Deployment__chart_tgz="x"), "http://h/p.csar")
    assert entry == "Definitions/main.yaml" and artifacts == [("Artifacts/Deployment/chart_tgz", "http://h/p.csar#Artifacts/Deployment/chart_tgz")]
    assert len(integrity_hash) == 64 and identity == {"name": "X", "version": "1.0.0", "vendor": "P"}


def test_a_tosca_meta_without_an_entry_definitions_line_is_a_validation_failure_not_a_stop_iteration():
    """A TOSCA.meta with no Entry-Definitions line raises PackageValidationFailed; an unguarded `next()` once raised StopIteration, which no caller catches.
    """
    with pytest.raises(PackageValidationFailed, match="no Entry-Definitions"):
        validate_package_bytes(csar(**{"TOSCA-Metadata__TOSCA.meta": "CSAR-Version: 1.1\n"}), "http://h/p.csar")


def test_a_location_that_does_not_end_in_csar_is_refused_here_too():
    """The `.csar` name check lives in the shared validation too, so the offline validator applies it as well."""
    with pytest.raises(PackageValidationFailed, match=r"does not end with \.csar"):
        validate_package_bytes(csar(), "http://h/p.zip")
