"""sdk.analytics — a thin client over MDAF (`mdaf/`) and RAN Analytics'
own producer-registration route (`ran-analytics/`), TS 28.104 MDA NRM.

Wave 1's split (docs/ownership/MDAF_OWNERSHIP.md) moved report/
subscription ownership to `mdaf/`; `ran-analytics/` kept only producer
registration and became an MDAF consumer. This client's namespace
mirrors that: producer registration is still the analytics-production
side of the same domain an rApp author thinks of as "analytics", even
though it's a different backend service than the one reports/
subscriptions live on.
"""

import uuid

from ._common import BaseClient, ensure_ok


class AnalyticsClient(BaseClient):
    def register_producer(self, producer_id: str, analytics_type: str, dme_input_types: list[uuid.UUID | str],
                           output_schema: dict) -> dict:
        return ensure_ok(self._r1.post(
            "/ran-analytics/producers", params={"producer_id": producer_id, "analytics_type": analytics_type},
            json={"dme_input_types": [str(t) for t in dme_input_types], "output_schema": output_schema},
        ))

    def list_producers(self, analytics_type: str | None = None, producer_id: str | None = None) -> list[dict]:
        return ensure_ok(self._r1.get("/ran-analytics/producers", params={"analytics_type": analytics_type, "producer_id": producer_id}))

    def publish_report(self, analytics_type: str, output: dict, input_sources: list[uuid.UUID | str] | None = None,
                        scope: dict | None = None) -> dict:
        return ensure_ok(self._r1.post(
            "/mdaf/reports", params={"analytics_type": analytics_type},
            json={"output": output, "input_sources": [str(s) for s in (input_sources or [])], "scope": scope},
        ))

    def query_reports(self, analytics_type: str | None = None) -> list[dict]:
        return ensure_ok(self._r1.get("/mdaf/reports", params={"analytics_type": analytics_type}))

    def subscribe(self, analytics_type: str, requested_by: str, notification_destination: str | None = None,
                  scope: dict | None = None) -> dict:
        # `scope` is the route's only body-eligible parameter (the other
        # three are all plain strings, so FastAPI treats them as query
        # params) — the JSON body is `scope` itself, unwrapped, not
        # {"scope": scope}. Confirmed against the route's own OpenAPI
        # schema before writing this, not assumed.
        return ensure_ok(self._r1.post("/mdaf/subscriptions", params={
            "analytics_type": analytics_type, "requested_by": requested_by,
            "notification_destination": notification_destination,
        }, json=scope))

    def unsubscribe(self, subscription_id: uuid.UUID | str) -> None:
        ensure_ok(self._r1.delete(f"/mdaf/subscriptions/{subscription_id}"))

    def list_subscriptions(self, analytics_type: str | None = None, requested_by: str | None = None) -> list[dict]:
        return ensure_ok(self._r1.get("/mdaf/subscriptions", params={"analytics_type": analytics_type, "requested_by": requested_by}))
