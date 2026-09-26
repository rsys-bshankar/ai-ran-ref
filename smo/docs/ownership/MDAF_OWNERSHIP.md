# MDAF Ownership

Status: **frozen** — Wave 0. See `docs/architecture/SERVICE_OWNERSHIP_MATRIX.md`
and `docs/architecture/AI_PLATFORM_BASELINE.md`.

## Mission

MDAF (Management Data Analytics Function) is analytics truth. It owns
the *output* of analysis — reports, predictions, drift, retraining
recommendations — realizing TS 28.104's real MDA NRM (already present
and audited: `specs/5G_APIs/TS28104_MdaNrm.yaml` /
`TS28104_MdaReport.yaml`, see `SPEC_AUDIT.md`'s RAN Analytics section).

## Owns

- Analytics reports
- Prediction reports
- Drift reports
- Retraining recommendations
- Analytics subscriptions
- Knowledge objects

## Does NOT own

| Concern | Owner |
|---|---|
| Training, model repository | AIMgF / MLMR |
| Data storage | DME |
| Domain-specific analytics use cases (traffic, energy, coverage) | RAN Analytics, as an MDAF consumer |

## Relationship to RAN Analytics

`ran-analytics/` keeps its current use-case-specific role (traffic
analytics, energy analytics, coverage analytics) and becomes a consumer
of MDAF rather than the service that owns analytics reporting itself.
This is a deliberate product-organization choice, not one TS 28.104
strictly forces — the spec's own `MDAType` enum already spans these
same use cases inside MDA. Recorded here so a future reviewer doesn't
mistake the RAN-Analytics/MDAF line for a spec requirement.

## Migration source (Wave 1)

`ran-analytics/app/models.py`'s `MDAFReport` (already named for this
concept) and `MDASubscription` move to `mdaf/`; `MDAFProducer` and the
use-case-specific registration/publish routes
(`register_analytics_producer`, `publish_report`) stay in
`ran-analytics/`, now calling MDAF the way any other consumer would
rather than owning the report/subscription tables directly.
