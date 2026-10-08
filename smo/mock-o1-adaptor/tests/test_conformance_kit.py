"""The O1 conformance kit (conformance/o1, PR-SB-9): it passes the mock, and it catches an adaptor that breaks the contract.

The kit is run in process against the mock (its HTTP client is the app's), and against small fakes that each break one thing RAN NF OAM relies on.
"""

import logging
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.main import app, _applied_changes, _faults, _object_state  # noqa: E402
from conformance.o1 import checks  # noqa: E402,F401
from conformance.o1.__main__ import main, protocols_from  # noqa: E402
from conformance.o1.kit import FAIL, PASS, REGISTRY, SKIP, Context, run, summary  # noqa: E402


@pytest.fixture(autouse=True)
def quiet_and_clean():
    logging.disable(logging.CRITICAL)
    _applied_changes.clear()
    _object_state.clear()
    _faults.clear()
    yield
    logging.disable(logging.NOTSET)


EMITTING = {"FM", "PM", "SW", "HB"}


def ctx_for(client, protocols=("netconf", "restconf")) -> Context:
    return Context(client, set(protocols))


def failures(results) -> dict[str, str]:
    return {r.id: r.detail for r in results if r.status == FAIL}


def test_the_mock_passes_every_check_in_both_protocols():
    results = run(ctx_for(TestClient(app, base_url="http://mock")))
    assert failures(results) == {}
    emitting = [c for c in REGISTRY if c.group in EMITTING]        # no --oam-url: nothing to read back from, so the emitting groups are skipped (tests_integration/test_o1_conformance_emit.py runs them)
    assert summary(results) == {PASS: len(REGISTRY) - len(emitting), FAIL: 0, SKIP: len(emitting)}
    assert all("--oam-url" in r.detail for r in results if r.status == SKIP and r.group in EMITTING)


def test_a_second_run_against_the_same_adaptor_passes_too():
    client = TestClient(app, base_url="http://mock")
    run(ctx_for(client))
    assert failures(run(ctx_for(client))) == {}


def test_a_protocol_that_is_not_part_of_the_run_is_skipped_not_failed():
    results = run(ctx_for(TestClient(app, base_url="http://mock"), protocols=("netconf",)))
    assert {r.status for r in results if r.group == "RESTCONF"} == {SKIP}
    assert failures(results) == {}


def test_every_check_has_an_id_a_group_and_a_title_and_the_ids_are_unique():
    ids = [c.id for c in REGISTRY]
    assert len(ids) == len(set(ids)) >= 20
    assert {c.group for c in REGISTRY} == {"DISC", "NETCONF", "RESTCONF"} | EMITTING
    assert all(c.title for c in REGISTRY)


# ---- an adaptor that lies: it acknowledges a write and does not apply it

def test_a_write_that_is_acknowledged_and_not_applied_is_caught():
    client = TestClient(app, base_url="http://mock")
    client.post("/faults", json={"mode": "IGNORE_WRITE", "count": 1000})
    bad = failures(run(ctx_for(client)))
    assert "NC-2" in bad and "read back" in bad["NC-2"]
    assert "RC-3" in bad


# ---- fakes that each break one thing

def netconf_reply(body: str, message_id="1") -> httpx.Response:
    return httpx.Response(200, content=f'<rpc-reply message-id="{message_id}" xmlns="urn:ietf:params:xml:ns:netconf:base:1.0">{body}</rpc-reply>',
                          headers={"content-type": "application/xml"})


def fake(handler) -> httpx.Client:
    return httpx.Client(base_url="http://fake", transport=httpx.MockTransport(handler))


def test_an_adaptor_that_acknowledges_everything_fails_the_refusals_and_the_read_back():
    def handler(request):
        if request.url.path == "/capabilities":
            return httpx.Response(200, json={"vendorName": "yes-man", "supportedServices": ["PROV"], "supportedVendorModes": ["O1_NETCONF"]})
        return netconf_reply("<ok/>")
    bad = failures(run(ctx_for(fake(handler), ("netconf",))))
    assert {"NC-2", "NC-6", "NC-7", "NC-8"} <= set(bad)
    assert "acknowledged" in bad["NC-6"]


def test_an_adaptor_that_answers_500_is_a_failure_not_a_crash_of_the_kit():
    def handler(request):
        return httpx.Response(500, text="boom")
    results = run(ctx_for(fake(handler)))
    assert len(results) == len(REGISTRY) and summary(results)[PASS] == 0
    assert all(r.detail for r in results if r.status == FAIL)


def test_an_adaptor_without_a_capability_declaration_fails_discovery():
    def handler(request):
        return httpx.Response(404)
    bad = failures(run(ctx_for(fake(handler), ()), {"DISC"}))
    assert set(bad) == {"DISC-1", "DISC-2", "DISC-3"} or {"DISC-1"} <= set(bad)
    assert "404" in bad["DISC-1"]


def test_a_vocabulary_the_oam_does_not_know_is_reported():
    def handler(request):
        return httpx.Response(200, json={"vendorName": "v", "supportedServices": ["TELEPORT"], "supportedVendorModes": ["O1_CARRIER_PIGEON"]})
    bad = failures(run(ctx_for(fake(handler), ()), {"DISC-2"}))
    assert "TELEPORT" in bad["DISC-2"] and "O1_CARRIER_PIGEON" in bad["DISC-2"]


def test_a_transport_that_is_run_but_not_declared_is_reported():
    def handler(request):
        return httpx.Response(200, json={"vendorName": "v", "supportedServices": ["PROV"], "supportedVendorModes": ["O1_NETCONF"]})
    bad = failures(run(ctx_for(fake(handler), ("netconf", "restconf")), {"DISC-3"}))
    assert "restconf" in bad["DISC-3"]


def test_an_entity_that_is_expanded_is_caught():
    state = {}

    def handler(request):
        text = request.content.decode()
        if "&x;" in text:
            state["label"] = "EXPANDED-BY-THE-ADAPTOR"
            return netconf_reply("<ok/>")
        if "<get-config>" in text:
            return netconf_reply(f"<data><managed-object ref='entity-probe'><userLabel>{state.get('label', '')}</userLabel></managed-object></data>")
        return netconf_reply("<ok/>")
    assert "NC-11" in failures(run(ctx_for(fake(handler), ("netconf",)), {"NC-11"}))


# ---- the runner

def test_protocols_follow_what_the_adaptor_declares_unless_told():
    assert protocols_from(["O1_NETCONF"], "auto") == {"netconf"}
    assert protocols_from(["O1_NETCONF", "O1_RESTCONF", "O1_SSH"], "auto") == {"netconf", "restconf"}
    assert protocols_from([], "auto") == set()
    assert protocols_from([], "both") == {"netconf", "restconf"}


def test_the_runner_exits_0_on_a_pass_1_on_a_fail_and_writes_both_reports(tmp_path, capsys):
    client = TestClient(app, base_url="http://mock")
    out = tmp_path / "report"
    assert main(["--adaptor", "http://mock", "--out", str(out)], client=client) == 0
    assert "24 passed" in capsys.readouterr().out
    assert (tmp_path / "report.json").exists() and "# O1 adaptor conformance" in (tmp_path / "report.md").read_text()
    client.post("/faults", json={"mode": "IGNORE_WRITE", "count": 1000})
    assert main(["--adaptor", "http://mock"], client=client) == 1


def test_the_runner_lists_the_checks(capsys):
    assert main(["--adaptor", "http://x", "--list"]) == 0
    assert "NC-2" in capsys.readouterr().out


def test_the_readme_lists_every_check_the_kit_has():
    readme = (Path(__file__).resolve().parents[2] / "conformance" / "README.md").read_text(encoding="utf-8")
    missing = [c.id for c in REGISTRY if f"| {c.id} | {c.group} | {c.title} |" not in readme]
    assert missing == []
