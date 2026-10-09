"""The validation that needs neither the database nor the web framework (app/package_validation.py), which the offline conformance validator also runs."""

import io
import zipfile

import pytest

from app.package_validation import PackageValidationFailed, validate_package_bytes


def csar(**overrides) -> bytes:
    files = {"TOSCA-Metadata/TOSCA.meta": "Entry-Definitions: Definitions/main.yaml\n", "Definitions/main.yaml": "application_name: X\napplication_version: 1.0.0\nprovider: P\n"}
    files.update({k.replace("__", "/"): v for k, v in overrides.items()})
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            if content is not None:
                z.writestr(name, content)
    return buf.getvalue()


def test_a_well_formed_package_gives_the_entry_artifacts_hash_and_identity():
    entry, artifacts, integrity_hash, identity = validate_package_bytes(csar(Artifacts__Deployment__chart_tgz="x"), "http://h/p.csar")
    assert entry == "Definitions/main.yaml" and artifacts == [("Artifacts/Deployment/chart_tgz", "http://h/p.csar#Artifacts/Deployment/chart_tgz")]
    assert len(integrity_hash) == 64 and identity == {"name": "X", "version": "1.0.0", "vendor": "P"}


def test_a_tosca_meta_without_an_entry_definitions_line_is_a_validation_failure_not_a_stop_iteration():
    with pytest.raises(PackageValidationFailed, match="no Entry-Definitions"):
        validate_package_bytes(csar(**{"TOSCA-Metadata__TOSCA.meta": "CSAR-Version: 1.1\n"}), "http://h/p.csar")


def test_a_location_that_does_not_end_in_csar_is_refused_here_too():
    with pytest.raises(PackageValidationFailed, match=r"does not end with \.csar"):
        validate_package_bytes(csar(), "http://h/p.zip")
