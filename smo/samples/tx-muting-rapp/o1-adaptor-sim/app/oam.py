"""The adaptor's northbound client: what an O1 adaptor does towards RAN NF OAM (self-registration and heartbeat,
PM subscriptions, PM reports, alarm ingest). Every call is an HTTP request on `base_url`; pass `transport` in tests."""

import datetime

import httpx

# the PM counters the TX-muting rApp consumes, with the value each one carries
DEFAULT_COUNTERS = ("DL_PRB_UTILIZATION", "RRC_CONNECTED_UE", "RADIO_SYNC_STATE")


class OamError(RuntimeError):
    pass


class OamClient:
    def __init__(self, base_url: str, transport: httpx.BaseTransport | None = None, timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self._http = httpx.Client(base_url=self.base_url, transport=transport, timeout=timeout)

    def _call(self, verb: str, path: str, expect=(200, 201, 202, 204), **kw):
        try:
            resp = self._http.request(verb.upper(), path, **kw)
        except httpx.HTTPError as exc:
            raise OamError(f"{verb.upper()} {self.base_url}{path}: {exc}") from exc
        if resp.status_code not in expect:
            raise OamError(f"{verb.upper()} {path} -> {resp.status_code}: {resp.text}")
        return resp.json() if resp.content else None

    def register(self, me: str, adaptor_uri: str, vendor: str, counters=DEFAULT_COUNTERS) -> dict:
        """Figure: self-register (DISCOVERED), heartbeat (ACTIVE), subscribe each PM counter."""
        known = self._call("get", f"/managed-entities/{me}", expect=(200, 404))
        if known and known.get("o1AdaptorEndpointId"):
            endpoint_id = known["o1AdaptorEndpointId"]
        else:
            endpoint_id = self._call("post", "/o1-adaptor-endpoints", json={
                "managedElementRef": me, "adaptorUri": adaptor_uri, "protocolSupport": ["NETCONF"],
                "o1Protocol": "NETCONF", "entityType": "O-DU", "vendorName": vendor})["endpointId"]
        self.heartbeat(endpoint_id)
        for counter in counters:
            self._call("post", "/pm-subscriptions", params={"managed_element_ref": me, "counter_type": counter,
                                                            "delivery_method": "pull", "granularity_period": 300})
        return {"endpointId": endpoint_id, "counters": list(counters)}

    def heartbeat(self, endpoint_id: str) -> None:
        self._call("post", f"/o1-adaptor-endpoints/{endpoint_id}/heartbeat")

    def report_counters(self, me: str, cell: str, counters: dict[str, float]) -> None:
        ts = datetime.datetime.now(datetime.UTC).isoformat()
        for counter, value in counters.items():
            self._call("post", "/pm-reports", json={"managedElementRef": me, "counterType": counter,
                                                    "measurements": [{"cellId": cell, "value": value, "timestamp": ts}]})

    def raise_alarm(self, me: str, function_ref: str, alarm_id: str, severity: str, cause: str, problem: str) -> dict:
        return self._call("post", "/alarms/ingest", params={
            "source_alarm_id": alarm_id, "managed_element_ref": me, "severity": severity, "managed_function_ref": function_ref,
            "probable_cause": cause, "specific_problem": problem})

    def clear_alarm(self, alarm_id: str, user: str) -> None:
        self._call("patch", f"/alarms/{alarm_id}/clear", params={"clear_user_id": user})
