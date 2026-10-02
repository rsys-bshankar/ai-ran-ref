# Fuzz targets

[Atheris](https://github.com/google/atheris) targets, run continuously by
ClusterFuzzLite (`.clusterfuzzlite/`; workflows `cflite_pr.yml` for the code a
PR changes and `cflite_batch.yml` weekly).

| Target | What it checks |
|---|---|
| `fuzz_webhook_guard.py` | The SSRF guard never raises, and only allows http(s) URLs whose host is not a blocked name or a loopback / link-local / multicast / unspecified / reserved IP |
| `fuzz_csar_parsers.py` | Onboarding's CSAR parsers, fed a zip built from fuzz bytes: only the documented validation failures are acceptable, anything else would escape the `FAILED` path |

Run one locally (`pip install atheris`, clang required):

```bash
cd smo
PYTHONPATH=shared python3 fuzz/fuzz_webhook_guard.py -max_total_time=30
PYTHONPATH=shared:onboarding python3 fuzz/fuzz_csar_parsers.py -max_total_time=30
```

A crash writes a `crash-<hash>` file next to where you ran it; re-run the target
with that file as its argument to reproduce, then add a regression test in the
module's `tests/` (see the `test_a_non_utf8_sme_declaration…` cases in
`onboarding/tests/test_main.py`). A new target is any `fuzz/fuzz_*.py` with a
`TestOneInput`; `.clusterfuzzlite/build.sh` builds them all.
