#!/usr/bin/env python3
"""Compares a DAST tool's findings with the baseline of accepted ones (PR-V-7): a finding that is not in the baseline fails the run.

    scripts/dast_baseline.py testssl security/testssl-baseline.json out/testssl.json
    scripts/dast_baseline.py nuclei  security/nuclei-baseline.json  out/nuclei.jsonl
    scripts/dast_baseline.py zap     security/zap-baseline.json     out/zap.json

A baseline is `{"<finding key>": "why it is accepted"}`; a key may use shell wildcards (`*`). The key of a finding is
  testssl  `<id> <severity>`            e.g. `cipherlist_3DES_IDEA low`, from `testssl.sh --jsonfile` (severity LOW and above, and WARN)
  nuclei   `<template-id> <severity>`   e.g. `http-missing-security-headers low`, from `nuclei -jsonl` (severity low and above)
  zap      `<plugin id> <risk>`         e.g. `10021 low`, from `zap-api-scan.py -J` (risk Low and above; the informational alerts are left out)
The failure output prints the key to paste into the baseline, with the detail the tool gave. An entry that matches nothing is reported as STALE and does
not fail the run (a scan that varies between runs would make that a flake); remove it when it stays stale.
"""

import fnmatch
import json
import sys
from pathlib import Path

REPORTED = {"low", "medium", "high", "critical", "warn"}


def testssl_findings(text: str) -> dict[str, str]:
    """`{"<id> <severity>": finding text}` from a `testssl.sh --jsonfile` document, for severities low and above and WARN (case-insensitive). The first finding of a key wins."""
    out = {}
    for item in json.loads(text):
        severity = str(item.get("severity", "")).lower()
        if severity in REPORTED:
            out.setdefault(f"{item.get('id')} {severity}", str(item.get("finding", "")))
    return out


def nuclei_findings(text: str) -> dict[str, str]:
    """`{"<template-id> <severity>": "<name> at <matched-at>"}` from nuclei's JSON-lines output; lines that do not start with `{` are skipped, severities below low are left out."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        item = json.loads(line)
        info = item.get("info", {})
        severity = str(info.get("severity", "")).lower()
        if severity in REPORTED:
            out.setdefault(f"{item.get('template-id')} {severity}", f"{info.get('name', '')} at {item.get('matched-at', '')}")
    return out


def zap_findings(text: str) -> dict[str, str]:
    """`{"<plugin id> <risk>": "<name> at <first uri>"}` from a ZAP `-J` report; risk codes 1 to 3 (low, medium, high) are kept and informational alerts (0) are left out."""
    out = {}
    labels = {"1": "low", "2": "medium", "3": "high"}
    for site in json.loads(text).get("site", []):
        for alert in site.get("alerts", []):
            risk = labels.get(str(alert.get("riskcode")))
            if risk:
                instances = alert.get("instances") or [{}]
                out.setdefault(f"{alert.get('pluginid') or alert.get('alertRef')} {risk}", f"{alert.get('name', '')} at {instances[0].get('uri', '')}")
    return out


PARSERS = {"testssl": testssl_findings, "nuclei": nuclei_findings, "zap": zap_findings}


def compare(findings: dict[str, str], baseline: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """(findings the baseline does not accept, baseline entries that matched nothing)."""
    used: set[str] = set()
    new = {}
    for key, detail in findings.items():
        matching = [pattern for pattern in baseline if fnmatch.fnmatchcase(key, pattern)]
        if matching:
            used.update(matching)
        else:
            new[key] = detail
    return new, sorted(set(baseline) - used)


def main(argv: list[str]) -> int:
    """Reads the baseline (keys starting with `_` are comments) and the tool's report, prints the counts, every NEW key with its detail and every STALE entry.

        Returns 1 only when there is a NEW finding; a stale baseline entry never fails the run. Returns 2 and prints the usage when the arguments are wrong.
    """
    if len(argv) != 3 or argv[0] not in PARSERS:
        print(__doc__)
        return 2
    tool, baseline_path, report_path = argv
    baseline = {k: v for k, v in json.loads(Path(baseline_path).read_text()).items() if not k.startswith("_")}
    findings = PARSERS[tool](Path(report_path).read_text())
    new, stale = compare(findings, baseline)
    print(f"{tool}: {len(findings)} finding(s) at low or above, {len(findings) - len(new)} accepted by the baseline, {len(new)} new, {len(stale)} stale baseline entr(ies)")
    for key, detail in sorted(new.items()):
        print(f"  NEW    {key}  ({detail})")
    for key in stale:
        print(f"  STALE  {key}")
    return 1 if new else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
