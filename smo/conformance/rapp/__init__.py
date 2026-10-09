"""The rApp conformance pack (PR-RAPP-3): what the platform relies on in an rApp package and in its life on a running stack, as checks a rApp developer can run.

    python -m conformance.rapp package my-rapp.csar [--trust DIR] [--require-signed] [--out report]     # offline: Onboarding's verdict, file by file
    python -m conformance.rapp runtime --package-url URL (--r1 URL | --direct) [--out report]           # onboard, register, heartbeat, R1 usage, terminate

See `conformance/rapp/README.md` for the checks, and `conformance/README.md` for the family (the O1 adaptor kit is the model).

The package is `kit.py` (registry, the two contexts, report), `package_checks.py` (offline, on a CSAR file), `runtime_checks.py` (against a running stack) and `__main__.py`
(the command line). It is a standalone tool no SMO module imports; the offline checks load Onboarding's validation module by file path and use `smo_shared.csar_signing`.
Design record: `HISTORY.md` PR-RAPP-3.
"""
