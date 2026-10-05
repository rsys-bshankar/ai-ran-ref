# Dynamic security scans (PR-V-7)

`.github/workflows/smo-dast.yml` starts the stack behind the TLS edge every night (and on a pull request that changes the workflow, this directory or
`scripts/dast_baseline.py`) and runs three scanners. It is not a merge gate: a run that finds something new fails, and the nightly failure is the signal.

| Scanner | Target | What it looks for | Baseline of accepted findings |
|---|---|---|---|
| OWASP ZAP API scan | the gateway (`r1-termination`), driven by `docs/openapi/r1-termination.json` | what an unauthenticated caller can reach: missing headers, error leakage, injection on the documented parameters | `zap-baseline.json` |
| testssl.sh | the TLS edge, `localhost:8443` (gateway) and `:3443` (GUI) | protocol versions, ciphers, certificate, known TLS vulnerabilities | `testssl-baseline.json` |
| Nuclei, default templates, severity low and above | the gateway and the GUI | known misconfigurations and exposures | `nuclei-baseline.json` |

The three tool images are pinned by digest in the workflow (`ZAP_IMAGE`, `TESTSSL_IMAGE`, `NUCLEI_IMAGE`; pinned 2026-10-05: ZAP `stable`, testssl.sh `3.2`,
Nuclei `v3.9.0`). Nuclei fetches its templates when it starts, so a run can see templates newer than the last one; that is the point of a nightly scan, and
the cost is a finding that appears without a change of ours. Every report (`zap.html`, `zap.json`, the testssl.sh output, `nuclei.jsonl`) is uploaded as the
artifact `dast-reports` for 30 days.

## When a run fails

1. Open the job's log: each tool prints `NEW <key> (<detail>)` for each finding the baseline does not accept. The report in the artifact has the request and the response.
2. Decide: **fix it** (the normal answer), or **accept it** with a reason a reviewer can judge, by adding `"<key>": "reason"` to the tool's baseline in a pull request. A key may use
   shell wildcards. A reason that says only "false positive" is not one; say why it cannot be exploited here, or what makes it a deliberate choice.
3. A baseline entry that matches nothing is printed as `STALE` and does not fail the run; remove it once it stays stale.

The scripts take the same inputs, so a report can be checked on a laptop: `scripts/dast_baseline.py zap|testssl|nuclei <baseline> <report>`.

## What is not scanned yet

- **Authenticated calls.** ZAP runs without an SMO access token, so it sees the gateway's refusals (401, 403, 404) and its headers, not the modules behind it. The token-abuse suite
  (`tests_integration/test_token_abuse.py`, PR-V-7b) covers identity and path handling with tokens; an authenticated scan (a token from SME, passed to ZAP) is the next step.
- **Each module on the internal network.** Only the gateway and the GUI are reachable from outside, so only they are scanned. A scan of every module's own specification would find what
  a compromised neighbour could reach.
- **The southbound side** (NETCONF/SSH to a managed element) and the Helm deployment's own surface.

## The external penetration test

An external test is a criterion for 1.0.0, commissioned after the security hardening items and against the 0.5.0 release candidate or later (`docs/VALIDATION.md`, V-7c). What it
should be given is in `docs/PENTEST_SCOPE.md`.
