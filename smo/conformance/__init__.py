"""The conformance kits: checks that a counterpart (an O1 adaptor, a rApp package or a running rApp) must pass to work with the SMO, run from outside it.

`conformance/o1` is the adaptor kit and `conformance/rapp` the rApp pack; they share a design (a registry of checks, pass/fail/skip, a JSON and a Markdown report,
exit status 1 on a failure) but no code. Neither is imported by an SMO module: they are command-line tools (`python -m conformance.o1`, `python -m conformance.rapp`),
run in CI against this repository's own mock adaptor and sample packages. `conformance/README.md` is the user-facing description.
"""
