# Demo signing key (do not trust in production)

`demo-publisher.seed` is the **private** ed25519 key of a made-up publisher called `demo-publisher`. It is committed here on purpose, and everyone who can read this
repository has it: it exists so that the four sample CSARs in `samples/` can be signed and the whole signing path (digest list, signature, trust store, Onboarding's
check, the conformance pack) can be shown and tested without anyone generating a key first.

`demo-publisher.pub` is its public half, in the form a trust store reads (a file named after the publisher).

| Use | Fine? |
|---|---|
| Trust `demo-publisher.pub` in a lab, a demo or a test (`ONBOARDING_TRUST_STORE=samples/demo-signing`) | yes |
| Sign your own package with `demo-publisher.seed` | only to try the tool |
| Put `demo-publisher.pub` in the trust store of a deployment that matters | **never**: it would accept any package, because anyone can sign with the private half |
| Copy this key for your own publisher | **never**: make your own (`python scripts/csar_sign.py keygen --out my-publisher`) and keep the private half in a secret store |

The private key is a one-line seed (`ed25519-seed:<base64>`), not a PEM block, so secret scanners do not mistake it for a leaked key; `scripts/csar_sign.py` and
`samples/build_csar.py` read it like a PEM key. This is the only private key committed in the repository (`tests_integration/test_rapp_signing.py` checks that no other
file carries one).

`python3 samples/build_csar.py` signs with it by default; `--key PATH` signs with another key and `--unsigned` writes a package with no signature.
