# Sample rApps (`samples/`)

Four reference rApps, each a directory that `build_csar.py` zips into the package (CSAR) Onboarding validates:

| rApp | Directory | Package |
|---|---|---|
| Energy Saving | [`energy-saving-rapp/`](energy-saving-rapp/README.md) | `energy-saving-rapp.csar` |
| Mobility Optimization | [`mobility-optimization-rapp/`](mobility-optimization-rapp/README.md) | `mobility-optimization-rapp.csar` |
| Coverage Optimization | [`coverage-optimization-rapp/`](coverage-optimization-rapp/README.md) | `coverage-optimization-rapp.csar` |
| Traffic Steering | [`traffic-steering-rapp/`](traffic-steering-rapp/README.md) | `traffic-steering-rapp.csar` |

Package layout and what Onboarding reads: [`../docs/RAPP_PACKAGING.md`](../docs/RAPP_PACKAGING.md).

## Building the packages

```bash
python3 samples/build_csar.py                          # all four, signed with the demo key
python3 samples/build_csar.py energy-saving-rapp       # one
python3 samples/build_csar.py --key my.key.pem NAME    # signed with your own key
python3 samples/build_csar.py --unsigned NAME          # no digest list, no signature
```

A rebuild of unchanged sources is byte-identical (fixed timestamps, deterministic ed25519 signing). The integration suite fails when a committed `.csar` no longer equals a rebuild of its sources: rebuild after changing anything under a sample.

## Signed with a demo key (do not trust it in production)

The committed packages carry a digest list and an ed25519 signature (`PR-RAPP-1`, `docs/RAPP_PACKAGING.md` §8) made with the **demo publisher** key in [`demo-signing/`](demo-signing/README.md). **Its private half is committed on purpose**, so that the signing path can be shown and tested: anyone who reads the repository can sign a package that verifies against `demo-signing/demo-publisher.pub`. Trust that key in a lab, a demo or a test (`ONBOARDING_TRUST_STORE=samples/demo-signing`); **never put it in the trust store of a deployment that matters**.

Check them the way Onboarding does, or with the whole conformance pack:

```bash
python scripts/csar_sign.py verify samples/*.csar --trust samples/demo-signing
python -m conformance.rapp package samples --trust samples/demo-signing --require-signed
```

Onboarding checks nothing unless `ONBOARDING_TRUST_STORE` is set, so the demos and the runbook onboard these packages as they always did.
