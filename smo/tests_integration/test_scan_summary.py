"""PR-SEC-12.2: the image scan's report and its gate (scripts/scan_summary.py), on Trivy-shaped results."""

import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "scan_summary.py"
spec = importlib.util.spec_from_file_location("scan_summary", SCRIPT)
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)


def vuln(vid, pkg, severity, fixed="1.2.4", installed="1.2.3"):
    return {"VulnerabilityID": vid, "PkgName": pkg, "InstalledVersion": installed, "FixedVersion": fixed, "Severity": severity}


def write(directory, name, vulns, result_type="debian"):
    (directory / f"{name}.json").write_text(json.dumps({"Results": [{"Target": f"{name} (debian 13)", "Type": result_type, "Vulnerabilities": vulns}]}))


def test_a_clean_scan_reports_nothing_and_passes_the_gate(tmp_path, capsys):
    write(tmp_path, "sme", [vuln("CVE-1", "libx", "MEDIUM"), vuln("CVE-2", "liby", "LOW")])      # below the line: not reported
    assert scan.main(["scan_summary.py", str(tmp_path), "--gate"]) == 0
    assert "No HIGH or CRITICAL finding" in capsys.readouterr().out


def test_a_high_finding_with_a_fix_fails_the_gate_but_not_the_report(tmp_path, capsys):
    write(tmp_path, "sme", [vuln("CVE-9", "libz", "HIGH")])
    assert scan.main(["scan_summary.py", str(tmp_path)]) == 0
    assert scan.main(["scan_summary.py", str(tmp_path), "--gate"]) == 1
    out = capsys.readouterr().out
    assert "| sme | 0 | 1 |" in out and "CVE-9" in out and "libz" in out and "1.2.4" in out


def test_the_same_finding_in_many_images_is_listed_once_with_where_it_is(tmp_path):
    for name in ("a", "b", "c", "d", "e", "f"):
        write(tmp_path, name, [vuln("CVE-5", "openssl", "CRITICAL")])
    out = scan.report(scan.load(tmp_path))
    assert "1 distinct findings" in out and out.count("| CRITICAL | CVE-5 |") == 1 and "and 2 more" in out


def test_critical_sorts_before_high(tmp_path):
    write(tmp_path, "sme", [vuln("CVE-H", "aaa", "HIGH"), vuln("CVE-C", "zzz", "CRITICAL")])
    out = scan.report(scan.load(tmp_path))
    assert out.index("CVE-C") < out.index("CVE-H")


def test_no_results_is_an_error_not_a_pass(tmp_path):
    assert scan.main(["scan_summary.py", str(tmp_path), "--gate"]) == 2
