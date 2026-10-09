# Vendor profile `example-du` (SB-10)

The first profile on the O1 stub, and the template for the next. **It is not a real vendor.** `PR-SB-10` is written as "needs access to a vendor simulator or lab", and none was available when this was built, so the vendor is a stand-in: an O-DU that is NETCONF-only, has no software management and models three things differently from 3GPP TS 28.541. Everything in the profile is real mechanism (the files, the loader, the registry entry, the checks, the report) and invented content. A real vendor's profile replaces `yang/`, `schema/` and the numbers in `profile.json`, and gets its own deviations list below; nothing else changes.

Use it: `MOCK_O1_PROFILE=example-du` on `mock-o1-adaptor` (see `mock-o1-adaptor/README.md`, 2.6), and register the same vendor at RAN NF OAM with the body `app/profile.py:onboarding_body("example-du")` (`POST /ran-nf-oam/vendor-onboarding`).

## Files (SB-10.1)

| File | What |
|---|---|
| `profile.json` | The vendor name, the MnS services and transports it offers, its conformance mode (`COMBINED`: TS 28.541 plus its own model), the schema it brings, the defaults of its classes |
| `yang/example-du-ext.yang` | The vendor's YANG: what it adds to, and narrows in, the standard model |
| `schema/example-du-ext.json` | The CM descriptor generated from that YANG: `python scripts/ingest_yang_schema.py mock-o1-adaptor/app/profiles/example-du/yang --name example-du-ext --out mock-o1-adaptor/app/profiles/example-du/schema/example-du-ext.json`. A test fails when it no longer matches the YANG |
| `conformance-report.md` | The O1 conformance kit run against the stub in this profile, RAN NF OAM read back (SB-10.4) |

## The capability entry (SB-10.2)

`POST /ran-nf-oam/vendor-onboarding` with the profile's body registers `example-vendor`: services `PROV, FM, PM, FILE, HEARTBEAT`; transport `O1_NETCONF`; conformance mode `COMBINED` with schema `example-du-ext@2026-10-01` over the bundled TS 28.541 descriptor. `tests_integration/test_vendor_profile.py` checks that this entry is what the stub declares at `GET /capabilities`, that onboarding twice is harmless, and that discovery from a registered element (`discoverFrom`) reads the same declaration.

## Deviations from the standard models (SB-10.3)

| # | Deviation | Effect at RAN NF OAM | Where it is enforced |
|---|---|---|---|
| 1 | **NETCONF only.** No RESTCONF root | An element of this vendor registered with `o1Protocol` RESTCONF is refused (`PROTOCOL_NOT_SUPPORTED`, 409). The stub answers 404 on every RESTCONF path | Capability check at registration; the stub |
| 2 | **No software management, streaming or subscription service.** Services are `PROV, FM, PM, FILE, HEARTBEAT` | `POST /software-management-jobs` for such an element is refused (`O1_SERVICE_NOT_SUPPORTED`, 409); the kit skips SW-1 to SW-3 | `require_service` |
| 3 | **`NRSectorCarrier.configuredMaxTxPower` accepts 0..40 dBm**, where the standard (and the stub's generic default, 43) allows more | A write of 43 is refused whole (422 `SCHEMA_VALIDATION_FAILED`, naming the attribute) before anything is sent; 40 is sent | `schema_problems` (leaf range) |
| 4 | **`NRCellDU` has two attributes the standard does not:** `exampleTxBackoffDb` (0..20 dB) and `exampleBeamMode` (`NARROW`, `WIDE`, `ADAPTIVE`, default `ADAPTIVE`) | A write of either is accepted when in range; out of range, or an unknown enum value, is refused (422). Without the profile the attribute is "not defined" | `schema_problems` |
| 5 | **A class of its own, `ExampleBeamProfile`** (`profileName` 1..32 characters of `[A-Za-z0-9_-]`, `horizontalBeamwidth` 10..120 degrees) | Writable and read back; a value out of range is refused | `schema_problems` |

What the stub does for the profile: declares it, returns the profile's class defaults on `get-config` (`exampleTxBackoffDb` 0, `exampleBeamMode` ADAPTIVE, `configuredMaxTxPower` 40, the beam profile's name and width), and has no RESTCONF root.

## Conformance run (SB-10.4)

`conformance-report.md`: 23 passed, 0 failed, 13 skipped (RESTCONF, because the vendor does not speak it, and the three software checks, because it does not offer the service). The report is the kit's own output, produced by `tests_integration/test_vendor_profile.py` against the stub in this profile and the real RAN NF OAM app in the in-process mesh; the test fails when the report differs, and `SMO_UPDATE_PROFILE_REPORT=1` rewrites it.

## What is not known (and would be, with a real vendor)

* Whether the vendor's YANG leaves are as simple as these: the descriptor reader does not evaluate `must`, `when`, `if-feature` or `deviation` statements (`scripts/ingest_yang_schema.py`), and a real vendor's deviations are often written as `deviation` statements. Those would have to be read by hand into the leaves, or the reader extended.
* The vendor's real NETCONF behaviour (sessions, capabilities, locking, error tags, notification streams) and its VES: the stub is HTTP-carried, and the kit's emitting checks use the stub's trigger API, which a vendor needs a hook of the same shape for.
* Whether the vendor's `get-config` returns values in the form RAN NF OAM compares (strings, enum spelling, units).
* Anything about scale or timing.

The run against the vendor's own simulator or lab (`SB-10.4` as written, with `SB-1.9`'s netconf-lab path) is the part that needs the vendor.
