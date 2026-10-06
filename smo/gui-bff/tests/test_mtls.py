"""PR-SEC-2: the BFF's calls to R1 present its certificate when `SMO_MTLS=on` (it does not install smo_shared, so it has its own few lines).

Run with: cd smo/gui-bff && PYTHONPATH=.:../shared python -m pytest tests/test_mtls.py -q
"""

import importlib.util
import ssl
from pathlib import Path

import pytest

from app import config, smo_client

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "mtls_certs.py"
_spec = importlib.util.spec_from_file_location("mtls_certs", SCRIPT)
certs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(certs)


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    for name in ("SMO_MTLS", "SMO_MTLS_CERT_FILE", "SMO_MTLS_KEY_FILE", "SMO_MTLS_CA_FILE", "R1_URL", "SME_URL"):
        monkeypatch.delenv(name, raising=False)


def test_off_by_default_the_addresses_and_the_client_are_as_before():
    assert config.Settings().r1_url == "http://r1-termination:8000"
    assert smo_client._mtls_client_args() == {}


def test_on_the_gateway_address_is_https_and_the_client_verifies_with_the_certificate(monkeypatch, tmp_path):
    certs.init(tmp_path, 30)
    monkeypatch.setenv("SMO_MTLS", "on")
    monkeypatch.setenv("SMO_MTLS_CERT_FILE", str(tmp_path / "gui-bff" / "tls.crt"))
    monkeypatch.setenv("SMO_MTLS_KEY_FILE", str(tmp_path / "gui-bff" / "tls.key"))
    monkeypatch.setenv("SMO_MTLS_CA_FILE", str(tmp_path / "gui-bff" / "ca.crt"))
    assert config.Settings().r1_url == "https://r1-termination:8000"
    monkeypatch.setenv("SME_URL", "http://sme:8000/")
    assert config.Settings().sme_url == "https://sme:8000"
    (context,) = smo_client._mtls_client_args().values()
    assert isinstance(context, ssl.SSLContext) and context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


def test_on_without_the_files_it_fails_at_start_rather_than_calling_without_a_certificate(monkeypatch, tmp_path):
    monkeypatch.setenv("SMO_MTLS", "on")
    monkeypatch.setenv("SMO_MTLS_CA_FILE", str(tmp_path / "missing-ca.crt"))
    with pytest.raises(OSError):
        smo_client._mtls_client_args()
