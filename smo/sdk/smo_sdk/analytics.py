"""sdk.analytics — a thin client over MDAF (`mdaf/`) and RAN Analytics'
own producer-registration route (`ran-analytics/`), TS 28.104 MDA NRM.

Wave 1's split (docs/ARCHITECTURE.md (MDAF)) moved report/
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
                           output_schema: dict, mda_type: str | None = None) -> dict:
        """`mda_type` (HISTORY.md §7): TS28104's own real, closed MDAType
        enum — optional; omit it to let the route derive one for this
        build's own known-unambiguous shorthand values, or pass one of
        the 24 real spec values directly for genuine conformance.
        """
        return ensure_ok(self._r1.post(
            "/ran-analytics/producers",
            params={"producer_id": producer_id, "analytics_type": analytics_type, "mda_type": mda_type},
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
                  scope: dict | None = None, threshold_info: list[dict] | None = None) -> dict:
        # Wave 3: `scope` used to be the route's only body-eligible
        # parameter (bare, unwrapped JSON body) until `thresholdInfo`
        # (TS28.104 ThresholdInfo, HISTORY.md §7's MDAF section) needed a
        # body field alongside it — FastAPI can't leave a single field
        # unwrapped once a second body-eligible field exists, so the
        # route now takes an explicit {"scope": ..., "thresholdInfo": ...}
        # body. Confirmed against the route's own live OpenAPI schema
        # before writing this, not assumed. Each threshold_info dict:
        # {monitoredMDAOutputIE, thresholdDirection, thresholdValue, hysteresis?}.
        # notification_destination moved from a query param into this
        # same body (cross-cutting standardization, Subscriptions) —
        # unified with every other subscription-shaped resource's own
        # notificationDestination body field.
        return ensure_ok(self._r1.post("/mdaf/subscriptions", params={
            "analytics_type": analytics_type, "requested_by": requested_by,
        }, json={"notificationDestination": notification_destination, "scope": scope, "thresholdInfo": threshold_info}))

    def unsubscribe(self, subscription_id: uuid.UUID | str) -> None:
        ensure_ok(self._r1.delete(f"/mdaf/subscriptions/{subscription_id}"))

    def list_subscriptions(self, analytics_type: str | None = None, requested_by: str | None = None) -> list[dict]:
        return ensure_ok(self._r1.get("/mdaf/subscriptions", params={"analytics_type": analytics_type, "requested_by": requested_by}))

    # ------------------------------------------------------------ Wave 5: TS 28.104 MDA NRM

    def create_mda_request(self, requested_mda_outputs: list[dict], reporting_method: str,
                           reporting_target: str | None = None, analytics_scope: dict | None = None,
                           mda_function_ref: uuid.UUID | str | None = None, requested_by: str | None = None,
                           start_time: str | None = None, stop_time: str | None = None) -> dict:
        """TS 28.104 MDARequest. `requested_mda_outputs` is a list of
        MDAOutputPerMDAType ({mDAType, mDAOutputIEFilters?}); delivery is
        per `reporting_method` (NOTIFICATION | FILE | STREAMING)."""
        body = {"requestedMDAOutputs": requested_mda_outputs, "reportingMethod": reporting_method,
                "reportingTarget": reporting_target, "analyticsScope": analytics_scope,
                "mDAFunctionRef": str(mda_function_ref) if mda_function_ref else None, "requestedBy": requested_by,
                "startTime": start_time, "stopTime": stop_time}
        return ensure_ok(self._r1.post("/mdaf/mda-requests", json={k: v for k, v in body.items() if v is not None}))

    def delete_mda_request(self, request_id: uuid.UUID | str) -> None:
        ensure_ok(self._r1.delete(f"/mdaf/mda-requests/{request_id}"))

    def publish_mda_report(self, mda_outputs: list[dict], managed_entities: list[str] | None = None,
                           report_kind: str | None = None, mda_request_ref: uuid.UUID | str | None = None,
                           input_sources: list[uuid.UUID | str] | None = None) -> dict:
        """TS 28.104 MDAReport with typed mDAOutputs; report_kind is
        ANALYTICS | PREDICTION | DRIFT (inferred when omitted)."""
        body = {"mDAOutputs": mda_outputs, "managedEntitiesScope": managed_entities, "reportKind": report_kind,
                "mDARequestRef": str(mda_request_ref) if mda_request_ref else None,
                "inputSources": [str(s) for s in (input_sources or [])]}
        return ensure_ok(self._r1.post("/mdaf/mda-reports", json={k: v for k, v in body.items() if v is not None}))

    def query_mda_reports(self, mda_type: str | None = None, report_kind: str | None = None,
                          managed_entity: str | None = None, mda_request_id: uuid.UUID | str | None = None) -> list[dict]:
        # unset filters are left out: httpx would send None as an empty
        # string, which the UUID-typed mda_request_id filter rejects (422)
        params = {"mda_type": mda_type, "report_kind": report_kind, "managed_entity": managed_entity,
                  "mda_request_id": str(mda_request_id) if mda_request_id else None}
        return ensure_ok(self._r1.get("/mdaf/mda-reports", params={k: v for k, v in params.items() if v is not None}))

    def get_prediction(self, managed_entity: str, pm_name: str | None = None) -> dict | None:
        """The latest PREDICTION report (W5-03's TrafficTrendReport: a
        TS 28.104 PREDICTIONS_PM_DATA report) for one managed entity, e.g.
        a cell's predicted PRB utilisation. With `pm_name`, returns just
        that PmPrediction ({pmName, pmPredictedValue, ...}); None if no
        report exists yet."""
        reports = self.query_mda_reports(report_kind="PREDICTION", managed_entity=managed_entity)
        if not reports:
            return None
        report = reports[0]  # newest first
        if pm_name is None:
            return report
        for output in report["attributes"]["mDAOutputs"]:
            output_list = output["mDAOutputList"]
            if isinstance(output_list, dict):
                for prediction in output_list.get("pmPredictions") or []:
                    if prediction["pmName"] == pm_name:
                        return prediction
        return None
