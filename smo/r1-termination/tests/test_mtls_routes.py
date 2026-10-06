"""PR-SEC-2: with `SMO_MTLS=on` the gateway reaches every backend, and advertises SME, over https; off, nothing changes.

The route table is built at import, so each case imports the gateway in a fresh interpreter.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
PROGRAM = """
import json
from app import main
print(json.dumps({"routes": main.ROUTES, "bootstrap": main.bootstrap()}))
"""


def table(extra_env: dict) -> dict:
    env = {**os.environ, "PYTHONPATH": f"{HERE}{os.pathsep}{HERE.parent / 'shared'}", "SMO_DATABASE_URL": "sqlite://", **extra_env}
    for name in ("SMO_MTLS", "SME_URL", "DME_URL", "R1_PUBLIC_BASE_URL", "R1_BOOTSTRAP_KEY", "R1_BOOTSTRAP_KEY_FILE"):
        if name not in extra_env:
            env.pop(name, None)
    result = subprocess.run([sys.executable, "-c", PROGRAM], capture_output=True, text=True, env=env, cwd=HERE)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout.splitlines()[-1])


def test_off_by_default_every_backend_is_plain_http():
    result = table({})
    assert result["routes"]["/sme"] == "http://sme:8000"
    assert all(url.startswith("http://") for url in result["routes"].values())
    assert result["bootstrap"]["apiEndpoints"][0]["tokenEndPoint"]["uri"] == "http://sme:8000/oauth2/token"


def test_with_mtls_every_backend_and_the_advertised_token_endpoint_are_https(tmp_path):
    result = table({"SMO_MTLS": "on"})
    assert all(url.startswith("https://") for url in result["routes"].values())
    assert result["routes"]["/dme-push"] == "https://dme:8000"
    assert result["bootstrap"]["apiEndpoints"][0]["tokenEndPoint"]["uri"] == "https://sme:8000/oauth2/token"


def test_an_address_set_by_the_operator_is_upgraded_too_but_an_https_one_is_left_alone():
    result = table({"SMO_MTLS": "on", "SME_URL": "http://sme.other:9000", "DME_URL": "https://dme.corp:8443"})
    assert result["routes"]["/sme"] == "https://sme.other:9000" and result["routes"]["/dme"] == "https://dme.corp:8443"
