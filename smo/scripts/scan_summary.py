#!/usr/bin/env python3
"""Summarise Trivy image scans (PR-SEC-12.2): a markdown report, and a verdict for the gate.

    python scripts/scan_summary.py <results-dir> [--gate]

`<results-dir>` holds one `trivy image --format json` file per image (`<name>.json`). The report goes to stdout (CI appends it to the job
summary). With `--gate` the exit status is 1 when any image has a HIGH or CRITICAL finding that has a fix (CI scans with `--ignore-unfixed`, so
these are the ones an upgrade removes). An accepted finding goes in `smo/.trivyignore` with the reason, which Trivy honours, so it is neither
reported nor gating.
"""

import json
import sys
from collections import defaultdict
from pathlib import Path

SEVERITIES = ("CRITICAL", "HIGH")


def load(results_dir: Path) -> dict[str, list[dict]]:
    """{image name: [finding, ...]} for the severities that matter; a finding is the vulnerability record plus the target it was found in."""
    images: dict[str, list[dict]] = {}
    for path in sorted(results_dir.glob("*.json")):
        findings = []
        for result in json.loads(path.read_text()).get("Results") or []:
            for vuln in result.get("Vulnerabilities") or []:
                if vuln.get("Severity") in SEVERITIES:
                    findings.append({**vuln, "Target": result.get("Target", ""), "Type": result.get("Type", "")})
        images[path.stem] = findings
    return images


def report(images: dict[str, list[dict]]) -> str:
    """The markdown report: a per-image count of CRITICAL and HIGH findings, then one row per distinct finding.

        Findings are grouped by (vulnerability id, package, installed version) and the images they appear in are listed (the first four, then "and n more"), ordered by
        severity then package. When there are none the report says so.
    """
    lines = ["## Image vulnerability scan", "",
             "HIGH and CRITICAL findings that have a fix (unfixed ones are not listed). One row per image:", "",
             "| Image | Critical | High |", "|---|---:|---:|"]
    for name, findings in images.items():
        lines.append(f"| {name} | {sum(f['Severity'] == 'CRITICAL' for f in findings)} | {sum(f['Severity'] == 'HIGH' for f in findings)} |")
    # the same package is usually in many images (one base): group by (id, package) so each finding is read once
    grouped: dict[tuple, dict] = {}
    for name, findings in images.items():
        for f in findings:
            key = (f["VulnerabilityID"], f["PkgName"], f.get("InstalledVersion", ""))
            entry = grouped.setdefault(key, {"finding": f, "images": []})
            entry["images"].append(name)
    if not grouped:
        lines += ["", "No HIGH or CRITICAL finding with a fix in any image."]
        return "\n".join(lines) + "\n"
    lines += ["", f"### {len(grouped)} distinct findings", "", "| Severity | Id | Package | Installed | Fixed in | Where | Images |", "|---|---|---|---|---|---|---|"]
    order = {s: i for i, s in enumerate(SEVERITIES)}
    for (vid, pkg, installed), entry in sorted(grouped.items(), key=lambda kv: (order[kv[1]["finding"]["Severity"]], kv[0][1], kv[0][0])):
        f = entry["finding"]
        shown = ", ".join(entry["images"][:4]) + (f" and {len(entry['images']) - 4} more" if len(entry["images"]) > 4 else "")
        lines.append(f"| {f['Severity']} | {vid} | {pkg} | {installed} | {f.get('FixedVersion', '')} | {f.get('Type') or f.get('Target')} | {shown} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    """Prints the report for the directory in the one non-option argument. With `--gate` returns 1 when any image has a finding; exit 2 for a wrong argument count or an
        empty directory. Without `--gate` it never fails on findings.
    """
    args = [a for a in argv[1:] if not a.startswith("--")]
    if len(args) != 1:
        print(__doc__)
        return 2
    images = load(Path(args[0]))
    if not images:
        print("no scan results found", file=sys.stderr)
        return 2
    print(report(images), end="")
    return 1 if "--gate" in argv and any(images.values()) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
