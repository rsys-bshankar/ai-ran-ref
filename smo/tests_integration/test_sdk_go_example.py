"""The Go SDK (`sdk-go/`, PR-RAPP-4): what its Go tests cannot see from the inside.

The Go tests check the client against a fake platform and against `docs/openapi/`. This file checks it against the rest of
the repository: the example rApp's package is a package Onboarding's checks accept, the page it declares is served by the
routes `main.go` registers, the routes the SDK calls agree with the role policy an rApp is held to (`smo_shared.roles`), and
the supply-chain properties the README promises (no third-party Go module, the Dockerfile pinned, the CI action pinned).
"""

import importlib.util
import io
import re
import zipfile
from pathlib import Path

import yaml

from smo_shared import roles
from smo_shared.operator_ui import declared_routes, validate_operator_ui

SMO = Path(__file__).resolve().parents[1]
SDK_GO = SMO / "sdk-go"
EXAMPLE = SDK_GO / "examples" / "hello-rapp"
WORKFLOW = SMO.parent / ".github" / "workflows" / "smo-tests.yml"


def _build_csar_module():
    spec = importlib.util.spec_from_file_location("hello_rapp_build_csar", EXAMPLE / "build_csar.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _package() -> dict[str, bytes]:
    data = _build_csar_module().build_bytes()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        return {name: z.read(name) for name in z.namelist()}


def test_the_example_package_is_a_csar_onboarding_accepts():
    files = _package()
    # the shared build helper adds the digest list and its signature (PR-RAPP-1)
    assert sorted(files) == ["Definitions/asd.yaml", "TOSCA-Metadata/DIGESTS.sha256", "TOSCA-Metadata/DIGESTS.sha256.sig", "TOSCA-Metadata/TOSCA.meta", "capabilities.yaml", "manifest.yaml"]
    assert b"Entry-Definitions: Definitions/asd.yaml" in files["TOSCA-Metadata/TOSCA.meta"]
    asd = files["Definitions/asd.yaml"].decode()
    for key in ("application_name", "application_version", "provider", "descriptor_id", "descriptor_invariant_id", "descriptor_version", "schema_version"):
        assert re.search(rf"^\s*{key}:\s*\S", asd, re.M), key
    manifest = yaml.safe_load(files["manifest.yaml"])
    assert manifest["rappManifest"]["manifestVersion"] == "1.0"
    for mode in manifest["runtimeProfiles"]:                                      # Onboarding's _validate_runtime_profiles
        assert mode in manifest["executionModes"] and manifest["runtimeProfiles"][mode]["cpu"] >= 0
    assert yaml.safe_load(files["capabilities.yaml"])["capabilities"]["consumes"][0]["namespace"] == "data"


def test_the_rebuild_is_byte_identical_like_the_samples():
    assert _build_csar_module().build_bytes() == _build_csar_module().build_bytes()


def test_the_declared_page_is_valid_and_every_route_it_reads_or_changes_is_served_by_main_go():
    declared = validate_operator_ui(yaml.safe_load(_package()["manifest.yaml"])["operatorUi"])
    served = set(re.findall(r'mux\.HandleFunc\("(GET|POST|PUT|PATCH|DELETE) (/instances/\{instanceId\}[^"]*)"', (EXAMPLE / "main.go").read_text()))
    assert served, "main.go registers no operator route"
    assert set(declared_routes(declared)) <= served


def test_the_example_registers_the_operator_api_the_role_policy_lets_an_rapp_register():
    assert roles.rapp_may_change("/rapp-mgmt", "PUT", "/instances/abc/operator-api")
    assert roles.rapp_may_change("/rapp-mgmt", "DELETE", "/instances/abc/operator-api")
    main = (EXAMPLE / "main.go").read_text()
    assert "RegisterOperatorAPI" in main and "ClearOperatorAPI" in main


def _sdk_routes() -> list[tuple[str, str, str]]:
    text = (SDK_GO / "routes.go").read_text()
    return [(f"/{spec}", method, re.sub(r"\{[^}]+\}", "x", tmpl)) for spec, method, tmpl in re.findall(r'gw\("([\w-]+)", "(\w+)", "([^"]+)"\)', text)]


def test_the_routes_the_sdk_calls_through_r1_are_open_to_an_rapp_except_the_two_the_readme_names():
    routes = _sdk_routes()
    assert len(routes) >= 10
    refused = {(m, p) for module, m, p in routes if not roles.rapp_may_change(module, m, p)}
    # BootstrapComplete and ReportPerformance: rApp Management's own lifecycle reports, which R1's allow-list does not open
    # to the rApp role (smo_shared/roles.py). They are in the SDK for a deployment that calls rApp Management directly; the
    # README says so. If the policy opens them, delete this exception and the note.
    assert refused == {("POST", "/instances/x/bootstrap-complete"), ("POST", "/instances/x/performance")}


def test_the_sdk_has_no_third_party_module_and_no_go_sum_to_drift():
    mod = (SDK_GO / "go.mod").read_text()
    assert "require" not in mod and "replace" not in mod
    assert not (SDK_GO / "go.sum").exists()
    for path in SDK_GO.rglob("*.go"):
        for imp in re.findall(r'^\s*(?:\w+\s+)?"([^"]+)"$', path.read_text(), re.M):
            assert "." not in imp.split("/")[0] or imp.startswith("github.com/rsys-bshankar/ai-ran-ref/smo/sdk-go"), f"{path}: {imp}"


def test_the_example_image_is_pinned_and_does_not_run_as_root():
    lines = [ln for ln in (EXAMPLE / "Dockerfile").read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    froms = [ln for ln in lines if ln.startswith("FROM ")]
    assert froms and all("@sha256:" in ln or ln.split()[1] == "scratch" for ln in froms), froms
    users = [ln.split()[1] for ln in lines if ln.startswith("USER ")]
    assert users and users[-1] not in ("root", "0", "0:0")
    assert any(ln.startswith("HEALTHCHECK ") for ln in lines)


def test_ci_builds_the_go_sdk_with_a_sha_pinned_setup_go():
    text = WORKFLOW.read_text()
    assert re.search(r"uses: actions/setup-go@[0-9a-f]{40} # v\d", text)
    for step in ("go vet ./...", "go test -race", "go build"):
        assert step in text, step
    assert "sdk-go" in text
