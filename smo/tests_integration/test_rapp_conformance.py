"""PR-RAPP-3: the rApp conformance pack (conformance/rapp).

Offline: the sample packages pass, and crafted bad packages fail the way Onboarding fails them (the verdict is compared with the real Onboarding app in the mesh).
Runtime: the kit takes a sample package through onboard, register, heartbeat, R1 usage and terminate against the in-process mesh, and fakes that each break one
thing show the checks read the platform back instead of counting 2xx answers.
"""

import io
import json
import re
import sys
import zipfile
from pathlib import Path

import httpx
import pytest

SMO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SMO_ROOT))
sys.path.insert(0, str(SMO_ROOT / "shared"))

from conformance.rapp import package_checks, runtime_checks  # noqa: E402,F401
from conformance.rapp.__main__ import main  # noqa: E402
from conformance.rapp.kit import FAIL, PASS, REGISTRY, SKIP, WARN, PackageContext, RuntimeContext, run, summary  # noqa: E402
from smo_shared import csar_signing as cs  # noqa: E402

SAMPLES = SMO_ROOT / "samples"
DEMO_TRUST = SAMPLES / "demo-signing"
ES = SAMPLES / "energy-saving-rapp.csar"
URL = "http://example/energy-saving-rapp.csar"


def outcome(results) -> dict[str, str]:
    return {r.id: r.status for r in results}


def failures(results) -> dict[str, str]:
    return {r.id: r.detail for r in results if r.status == FAIL}


def entries(path: Path = ES) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as z:
        return {i.filename: z.read(i.filename) for i in z.infolist()}


def zipped(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


def unsigned(change=None, path: Path = ES) -> bytes:
    files = {k: v for k, v in entries(path).items() if k not in cs.SIGNING_FILES}
    if change:
        change(files)
    return zipped(files)


def offline(data: bytes, name="pkg.csar", **kwargs):
    trust = kwargs.pop("trust", None)
    return run(PackageContext(name, data, trust, **kwargs))


# ------------------------------------------------------------------------------------------------------------------------------- offline

def test_each_sample_package_passes_every_offline_check_that_applies_with_its_signature_verified():
    trust = cs.load_trust_store(DEMO_TRUST)
    for csar in sorted(SAMPLES.glob("*.csar")):
        results = offline(csar.read_bytes(), csar.name, trust=trust, require_signed=True)
        assert failures(results) == {}, csar.name
        assert [r.id for r in results if r.status == WARN] == [], csar.name
        assert outcome(results)["PK-S"] == PASS and outcome(results)["PK-V"] == PASS
        assert {r.id for r in results if r.status == SKIP} <= {"PK-7"}                # no sample declares SME providers


def test_without_a_trust_store_the_signature_is_skipped_and_with_require_signed_it_fails():
    assert outcome(offline(ES.read_bytes()))["PK-S"] == SKIP
    failed = failures(offline(ES.read_bytes(), require_signed=True))
    assert "no trust store" in failed["PK-S"] and "no trusted publisher keys" in failed["PK-V"]


BAD_PACKAGES = {
    "not-a-zip": (lambda: b"this is not a zip", "pkg.csar", {"PK-1", "PK-V"}),
    "wrong-extension": (lambda: unsigned(), "pkg.zip", {"PK-1", "PK-V"}),
    "no-tosca-meta": (lambda: unsigned(lambda f: f.pop("TOSCA-Metadata/TOSCA.meta")), "pkg.csar", {"PK-2", "PK-V"}),
    "no-entry-line": (lambda: unsigned(lambda f: f.__setitem__("TOSCA-Metadata/TOSCA.meta", b"CSAR-Version: 1.1\n")), "pkg.csar", {"PK-2", "PK-V"}),
    "entry-file-missing": (lambda: unsigned(lambda f: f.pop("Definitions/asd.yaml")), "pkg.csar", {"PK-2", "PK-V"}),
    "manifest-not-yaml": (lambda: unsigned(lambda f: f.__setitem__("manifest.yaml", b"a: [unclosed\n")), "pkg.csar", {"PK-4", "PK-V"}),
    "unknown-execution-mode": (lambda: unsigned(lambda f: f.__setitem__("manifest.yaml", b"rappManifest: {manifestVersion: '1.0'}\nruntimeProfiles:\n  BOGUS: {cpu: 1}\n")),
                               "pkg.csar", {"PK-4", "PK-V"}),
    "negative-cpu": (lambda: unsigned(lambda f: f.__setitem__("manifest.yaml", b"rappManifest: {manifestVersion: '1.0'}\nruntimeProfiles:\n  TRAINING: {cpu: -1}\n")),
                     "pkg.csar", {"PK-4", "PK-V"}),
    "memory-not-a-quantity": (lambda: unsigned(lambda f: f.__setitem__("manifest.yaml", b"rappManifest: {manifestVersion: '1.0'}\nruntimeProfiles:\n  TRAINING: {cpu: 1, memory: 16 GB}\n")),
                              "pkg.csar", {"PK-4", "PK-V"}),
    "bad-operator-ui": (lambda: unsigned(lambda f: f.__setitem__("manifest.yaml", b"rappManifest: {manifestVersion: '1.0'}\noperatorUi: {version: 9, panels: []}\n")),
                        "pkg.csar", {"PK-4", "PK-V"}),
    "capabilities-not-a-mapping": (lambda: unsigned(lambda f: f.__setitem__("capabilities.yaml", b"- just\n- a list\n")), "pkg.csar", {"PK-6", "PK-V"}),
    "sme-json-broken": (lambda: unsigned(lambda f: f.__setitem__("Files/Sme/providers/p.json", b"{broken")), "pkg.csar", {"PK-7", "PK-V"}),
    # the header is not written whole: the repository holds no private key but the demo one (test_rapp_signing.py)
    "private-key-inside": (lambda: unsigned(lambda f: f.__setitem__("notes.txt", b"-----BEGIN " + b"PRIVATE KEY-----\nAAAA\n-----END " + b"PRIVATE KEY-----\n")),
                           "pkg.csar", {"PK-9"}),
}


@pytest.mark.parametrize("name", sorted(BAD_PACKAGES))
def test_a_crafted_bad_package_fails_the_check_that_names_the_problem(name):
    make, filename, expected = BAD_PACKAGES[name]
    results = offline(make(), filename)
    assert set(failures(results)) == expected, failures(results)
    assert all(detail for detail in failures(results).values())


def test_a_package_that_is_not_a_zip_does_not_crash_the_checks_that_need_one():
    results = offline(b"nope")
    assert {r.id for r in results if r.status == SKIP} >= {"PK-2", "PK-3", "PK-4", "PK-8", "PK-9"}


def test_advice_is_a_warning_not_a_failure():
    def advise(files):
        files["tests/test_x.py"] = b"def test_x(): pass\n"
        files["capabilities.yaml"] = b"capabilities:\n  consumes: [{namespace: quantum, description: x}]\n"
        files["manifest.yaml"] = b"rappManifest: {manifestVersion: '1.0'}\nexecutionModes: [TRAINING, INFERENCE]\nruntimeProfiles:\n  TRAINING: {cpu: 1, memory: 1Gi}\n"
        files["Definitions/asd.yaml"] = b"application_name: Only_A_Name\n"
    results = offline(unsigned(advise))
    assert failures(results) == {}
    warned = {r.id: r.detail for r in results if r.status == WARN}
    assert set(warned) == {"PK-3", "PK-5", "PK-6", "PK-8", "PK-S"} - {"PK-S"}, warned
    assert "application_version" in warned["PK-3"] and "INFERENCE is an execution mode with no runtime profile" in warned["PK-5"] and "quantum" in warned["PK-6"]


def test_an_unsigned_package_is_a_warning_with_a_trust_store_and_a_failure_when_signing_is_required():
    trust = cs.load_trust_store(DEMO_TRUST)
    assert outcome(offline(unsigned(), trust=trust))["PK-S"] == WARN
    results = offline(unsigned(), trust=trust, require_signed=True)
    assert "not signed" in failures(results)["PK-S"] and "PK-V" in failures(results)


def test_a_package_changed_after_signing_fails_the_signature_check_and_onboardings_verdict():
    trust = cs.load_trust_store(DEMO_TRUST)
    files = entries()
    files["manifest.yaml"] += b"# one more line\n"
    failed = failures(offline(zipped(files), trust=trust))
    assert "manifest.yaml does not match its signed digest" in failed["PK-S"] and "package signature" in failed["PK-V"]


def test_a_package_signed_by_a_publisher_the_trust_store_does_not_hold_is_an_unknown_publisher(tmp_path):
    (tmp_path / "other.pub").write_bytes(cs.generate_keypair()[1])
    failed = failures(offline(ES.read_bytes(), trust=cs.load_trust_store(tmp_path)))
    assert "unknown publisher" in failed["PK-S"] and "unknown publisher" in failed["PK-V"]


@pytest.mark.parametrize("name", sorted(BAD_PACKAGES))
def test_the_offline_verdict_is_the_one_the_real_onboarding_gives(name, mesh, loaded_apps, monkeypatch):
    """PK-V fails exactly when POST /packages ends FAILED, for each crafted package (the private-key one is hygiene advice Onboarding does not give)."""
    make, filename, _ = BAD_PACKAGES[name]
    data = make()
    verdict = outcome(offline(data, filename))["PK-V"]

    class Resp:
        content = data

        def raise_for_status(self):
            pass
    real_get = httpx.get
    location = f"http://example/{filename}"
    monkeypatch.setattr(loaded_apps["onboarding"].httpx, "get", lambda loc, timeout=None, **kw: Resp() if loc == location else real_get(loc, timeout=timeout, **kw))
    answer = mesh["onboarding"].post("/packages", json={"location": location}).json()
    state = mesh["onboarding"].get(f"/packages/{answer['packageId']}/onboarding-status").json()["state"]
    assert (verdict == FAIL) == (state == "FAILED"), (name, verdict, state)


def test_the_good_packages_are_accepted_by_both(mesh, loaded_apps, monkeypatch):
    data = ES.read_bytes()

    class Resp:
        content = data

        def raise_for_status(self):
            pass
    monkeypatch.setattr(loaded_apps["onboarding"].httpx, "get", lambda loc, timeout=None, **kw: Resp())
    answer = mesh["onboarding"].post("/packages", json={"location": URL}).json()
    assert mesh["onboarding"].get(f"/packages/{answer['packageId']}/onboarding-status").json()["state"] == "AVAILABLE"
    assert outcome(offline(data))["PK-V"] == PASS


# ---------------------------------------------------------------------------------------------------------------------------- the CLI

def test_the_package_command_writes_the_report_files_and_exits_0_on_the_samples(tmp_path, capsys):
    out = tmp_path / "report"
    assert main(["package", str(SAMPLES), "--trust", str(DEMO_TRUST), "--require-signed", "--out", str(out)]) == 0
    report = json.loads((tmp_path / "report.json").read_text())
    assert report["kit"] == "conformance.rapp" and report["kind"] == "package" and report["summary"]["fail"] == 0 and report["summary"]["warn"] == 0
    assert len(report["packages"]) == 4 and len(report["results"]) == 4 * len([c for c in REGISTRY if c.kind == "package"])
    markdown = (tmp_path / "report.md").read_text()
    assert "0 failed, 0 warnings" in markdown and "| PK-S |" in markdown and "energy-saving-rapp.csar" in markdown
    assert capsys.readouterr().out.rstrip("\n") == markdown.rstrip("\n")


def test_the_package_command_exits_1_on_a_bad_package_and_2_on_a_usage_problem(tmp_path, capsys):
    bad = tmp_path / "bad.csar"
    bad.write_bytes(BAD_PACKAGES["negative-cpu"][0]())
    assert main(["package", str(bad)]) == 1
    assert "runtimeProfiles.TRAINING.cpu must be a non-negative number" in capsys.readouterr().out
    assert main(["package", str(tmp_path / "missing.csar")]) == 2
    assert main(["package", str(ES), "--require-signed"]) == 2
    assert main(["package", str(ES), "--trust", str(tmp_path / "no-store")]) == 2
    (tmp_path / "empty").mkdir()
    assert main(["package", str(tmp_path / "empty")]) == 2                         # a directory with no .csar in it
    assert main([]) == 2
    err = capsys.readouterr().err
    assert "Traceback" not in err and "--require-signed needs --trust" in err


def test_list_names_every_check_with_its_kind(capsys):
    assert main(["--list"]) == 0
    listing = capsys.readouterr().out
    assert len(listing.splitlines()) == len(REGISTRY) and "PK-V" in listing and "RT-8" in listing
    assert len({c.id for c in REGISTRY}) == len(REGISTRY)


def test_only_runs_one_check_or_one_group(tmp_path):
    out = tmp_path / "r"
    assert main(["package", str(ES), "--only", "PK-9", "--only", "SIGNATURE", "--out", str(out)]) == 0
    assert [r["id"] for r in json.loads((tmp_path / "r.json").read_text())["results"]] == ["PK-9", "PK-S"]


# ------------------------------------------------------------------------------------------------------------------------------ runtime

@pytest.fixture
def serve(loaded_apps, monkeypatch):
    """Onboarding fetches the package over HTTP: serve `data` at URL (and leave every other call to the mesh)."""
    def _serve(data: bytes = ES.read_bytes()):
        class Resp:
            content = data

            def raise_for_status(self):
                pass
        real_get = httpx.get
        monkeypatch.setattr(loaded_apps["onboarding"].httpx, "get", lambda loc, timeout=None, **kw: Resp() if loc == URL else real_get(loc, timeout=timeout, **kw))
    return _serve


def runtime(services, **kwargs):
    ctx = RuntimeContext(services, URL, **kwargs)
    return ctx, run(ctx)


def test_the_platform_passes_every_runtime_check_with_a_sample_package(mesh, serve):
    serve()
    ctx, results = runtime(mesh)
    assert failures(results) == {}
    assert [r.id for r in results] == [f"RT-{n}" for n in range(1, 9)]
    assert summary(results) == {PASS: 8, FAIL: 0, WARN: 0, SKIP: 0}


def test_a_run_removes_what_it_made_so_a_second_run_of_the_same_package_passes_too(mesh, serve):
    serve()
    for _ in range(2):
        assert failures(runtime(mesh)[1]) == {}
    instances = mesh["rapp-mgmt"].get("/instances").json()["items"]
    assert instances == []
    states = {p["state"] for p in mesh["onboarding"].get("/packages").json()["items"]}
    assert states == {"DELETING"}


def test_keep_leaves_the_package_onboarded_and_the_instance_removed(mesh, serve):
    serve()
    assert failures(runtime(mesh, keep=True)[1]) == {}
    assert mesh["rapp-mgmt"].get("/instances").json()["items"] == []
    assert {p["state"] for p in mesh["onboarding"].get("/packages").json()["items"]} == {"AVAILABLE"}


def test_each_signed_sample_runs_through_the_platform(mesh, serve):
    for csar in sorted(SAMPLES.glob("*.csar")):
        serve(csar.read_bytes())
        assert failures(runtime(mesh)[1]) == {}, csar.name


def test_the_autonomy_mode_asked_for_is_the_one_the_instance_has(mesh, serve):
    serve()
    assert failures(runtime(mesh, autonomy_mode="ASSIST")[1]) == {}


class Faulty:
    """A platform in front of one module's client that breaks one thing: `rule(verb, path, response)` may return another httpx.Response."""

    def __init__(self, real, rule):
        self.real, self.rule = real, rule

    def __getattr__(self, verb):
        def call(path, **kwargs):
            response = getattr(self.real, verb)(path, **kwargs)
            replaced = self.rule(verb, path, response)
            return replaced if replaced is not None else response
        return call


def answer(status: int, body) -> httpx.Response:
    return httpx.Response(status, json=body)


def break_one(mesh, service, rule):
    return {**mesh, service: Faulty(mesh[service], rule)}


def test_a_package_that_onboarding_refuses_fails_rt_1_with_its_reason_and_skips_the_rest(mesh, serve, monkeypatch):
    serve(unsigned(lambda f: f.__setitem__("manifest.yaml", b"a: [unclosed\n")))
    _, results = runtime(mesh)
    assert outcome(results)["RT-1"] == FAIL and "FAILED" in failures(results)["RT-1"]
    assert {r.status for r in results if r.id != "RT-1"} == {SKIP}
    assert all("needs" in r.detail for r in results if r.id != "RT-1")


def test_the_trust_policy_of_the_platform_shows_in_rt_1(mesh, serve, monkeypatch, tmp_path):
    serve(unsigned())
    (tmp_path / "other.pub").write_bytes(cs.generate_keypair()[1])
    monkeypatch.setenv("ONBOARDING_TRUST_STORE", str(tmp_path))
    monkeypatch.setenv("ONBOARDING_REQUIRE_SIGNED_PACKAGES", "true")
    _, results = runtime(mesh)
    assert "package signature: the package is not signed" in failures(results)["RT-1"]


def test_an_instance_that_never_runs_fails_rt_2_and_the_run_still_cleans_up(mesh, serve):
    serve()

    def rule(verb, path, response):
        if verb == "post" and path.endswith("/bootstrap-complete"):
            return answer(200, {"state": "DEPLOYING"})
    ctx, results = runtime(break_one(mesh, "rapp-mgmt", rule))
    assert "expected RUNNING" in failures(results)["RT-2"]
    assert mesh["rapp-mgmt"].get("/instances").json()["items"] == []                # terminated and deleted by the clean-up


def test_a_usage_registration_that_is_missing_fails_rt_3(mesh, serve):
    serve()

    def rule(verb, path, response):
        if verb == "get" and path.endswith("/usage"):
            return answer(200, {"items": []})
    assert "0 usage registrations" in failures(runtime(break_one(mesh, "onboarding", rule))[1])["RT-3"]


def test_reports_that_are_acknowledged_and_not_kept_fail_rt_4(mesh, serve):
    serve()

    def rule(verb, path, response):
        if verb == "get" and path.endswith("/performance"):
            return answer(200, {"items": []})
    assert "read back as []" in failures(runtime(break_one(mesh, "rapp-mgmt", rule))[1])["RT-4"]


def test_a_configuration_that_is_not_what_was_written_fails_rt_5(mesh, serve):
    serve()

    def rule(verb, path, response):
        if verb == "get" and path.endswith("/config"):
            return answer(200, {"stale": True})
    assert "differs from what was written" in failures(runtime(break_one(mesh, "rapp-mgmt", rule))[1])["RT-5"]


def test_a_warning_fault_that_faults_the_instance_fails_rt_6(mesh, serve):
    serve()

    def rule(verb, path, response):
        if verb == "post" and path.endswith("/fault"):
            return answer(200, {"status": "recorded", "instanceState": "FAULTED"})
    assert "still RUNNING" in failures(runtime(break_one(mesh, "rapp-mgmt", rule))[1])["RT-6"]


def test_a_terminate_that_leaves_the_workload_or_the_usage_fails_rt_7(mesh, serve):
    serve()

    def rule(verb, path, response):
        if verb == "post" and path.endswith("/terminate") and response.status_code == 200:
            body = response.json()
            body["lastTeardown"]["usageStop"] = "FAILED: HTTP 503"
            return answer(200, body)
    failed = failures(runtime(break_one(mesh, "rapp-mgmt", rule))[1])
    assert "usageStop" in failed["RT-7"] and "RT-8" not in failed


def test_an_instance_that_survives_its_deletion_fails_rt_8(mesh, serve):
    serve()

    def rule(verb, path, response):
        if verb == "delete" and "/instances/" in path:
            return httpx.Response(200, json={})
    assert "expected 204" in failures(runtime(break_one(mesh, "rapp-mgmt", rule))[1])["RT-8"]


def test_a_platform_that_does_not_answer_is_a_failure_with_the_reason_not_a_crash(mesh, serve):
    serve()

    class Down:
        def __getattr__(self, verb):
            def call(path, **kwargs):
                raise httpx.ConnectError("connection refused")
            return call
    results = runtime({**mesh, "onboarding": Down()})[1]
    assert "no usable answer: ConnectError" in failures(results)["RT-1"]


def test_the_runtime_command_writes_both_report_files(mesh, serve, tmp_path, capsys):
    serve()
    out = tmp_path / "rt"
    assert main(["runtime", "--package-url", URL, "--direct", "--out", str(out)], services=mesh) == 0
    report = json.loads((tmp_path / "rt.json").read_text())
    assert report["kind"] == "runtime" and report["packageUrl"] == URL and report["summary"] == {"pass": 8, "fail": 0, "warn": 0, "skip": 0}
    assert re.search(r"\*\*8 passed, 0 failed, 0 warnings, 0 skipped\.\*\*", (tmp_path / "rt.md").read_text())
    assert [r["id"] for r in report["results"]] == [f"RT-{n}" for n in range(1, 9)]


def test_the_runtime_command_needs_a_way_to_reach_the_platform(capsys):
    assert main(["runtime", "--package-url", URL]) == 2
    assert main(["runtime", "--package-url", URL, "--direct", "--r1", "http://x"]) == 2
    assert "give --r1" in capsys.readouterr().err


def test_the_default_clients_are_built_from_the_gateway_or_the_module_names(monkeypatch):
    import conformance.rapp.__main__ as cli
    seen = []
    monkeypatch.setattr(cli, "run", lambda ctx, only=None: seen.append(ctx.services) or [])
    cli.main(["runtime", "--package-url", URL, "--r1", "http://r1:8000/", "--header", "Authorization: Bearer t"])
    cli.main(["runtime", "--package-url", URL, "--direct", "--direct-template", "http://{service}.smo:8000"])
    gateway, direct = seen
    assert str(gateway["onboarding"].base_url) == "http://r1:8000/onboarding/" and gateway["onboarding"].headers["authorization"] == "Bearer t"
    assert str(direct["rapp-mgmt"].base_url) == "http://rapp-mgmt.smo:8000"
