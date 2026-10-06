"""The baseline comparison of the DAST lane (PR-V-7): what it accepts, what it fails on, and that the committed baselines are well formed."""

import importlib.util
import json
from pathlib import Path

SMO = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("dast_baseline", SMO / "scripts" / "dast_baseline.py")
dast = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dast)

TESTSSL = json.dumps([
    {"id": "TLS1_3", "severity": "OK", "finding": "offered"},
    {"id": "cipherlist_3DES_IDEA", "severity": "LOW", "finding": "offered"},
    {"id": "chain_of_trust", "severity": "HIGH", "finding": "self signed"},
    {"id": "scanTime", "severity": "INFO", "finding": "12"},
])
NUCLEI = "\n".join([
    json.dumps({"template-id": "tech-detect", "info": {"severity": "info", "name": "Tech"}, "matched-at": "http://x"}),
    json.dumps({"template-id": "http-missing-security-headers", "info": {"severity": "low", "name": "Headers"}, "matched-at": "http://x/a"}),
    "not json, skipped",
])


def test_testssl_reports_low_and_above_only():
    assert set(dast.testssl_findings(TESTSSL)) == {"cipherlist_3DES_IDEA low", "chain_of_trust high"}


def test_nuclei_reports_low_and_above_only():
    assert set(dast.nuclei_findings(NUCLEI)) == {"http-missing-security-headers low"}


def test_zap_reports_low_and_above_only():
    report = json.dumps({"site": [{"alerts": [
        {"pluginid": "10021", "name": "X-Content-Type-Options", "riskcode": "1", "instances": [{"uri": "http://g/a"}]},
        {"pluginid": "10049", "name": "Storable content", "riskcode": "0"},
        {"pluginid": "40012", "name": "XSS", "riskcode": "3"},
    ]}]})
    assert set(dast.zap_findings(report)) == {"10021 low", "40012 high"}


def test_a_finding_the_baseline_does_not_accept_is_new_and_an_unused_entry_is_stale():
    found = dast.testssl_findings(TESTSSL)
    new, stale = dast.compare(found, {"chain_of_trust high": "dev CA", "gone high": "was fixed"})
    assert set(new) == {"cipherlist_3DES_IDEA low"}
    assert stale == ["gone high"]


def test_baseline_keys_take_wildcards():
    new, stale = dast.compare(dast.testssl_findings(TESTSSL), {"cipherlist_* low": "legacy clients", "chain_of_trust *": "dev CA"})
    assert not new and not stale


def test_exit_status_is_one_for_a_new_finding(tmp_path):
    report, baseline = tmp_path / "t.json", tmp_path / "b.json"
    report.write_text(TESTSSL)
    baseline.write_text(json.dumps({"_comment": "x", "chain_of_trust high": "dev CA"}))
    assert dast.main(["testssl", str(baseline), str(report)]) == 1
    baseline.write_text(json.dumps({"chain_of_trust high": "dev CA", "cipherlist_3DES_IDEA low": "legacy"}))
    assert dast.main(["testssl", str(baseline), str(report)]) == 0


def test_committed_baselines_are_objects_of_reasons():
    for name in ("testssl-baseline.json", "nuclei-baseline.json", "zap-baseline.json"):
        data = json.loads((SMO / "security" / name).read_text())
        assert isinstance(data, dict)
        assert all(isinstance(reason, str) and reason for reason in data.values()), name
