"""sdk.platform — a thin client over SME (`sme/`)'s own generic,
domain-independent surface: provider enrolment, service publish/
discovery, and CAPIF event subscriptions.

Deliberately narrow: SME's own OAuth2 token/introspection and Trusted
Invokers routes are R1Client's own concern (invoker onboarding and token
acquisition already happen transparently on every cross-module call —
see shared/smo_shared/r1_client.py's own module docstring), not
something an rApp author using this SDK ever needs to touch directly.
"""

from ._common import BaseClient, ensure_ok


class PlatformClient(BaseClient):
    def register_provider(self, apf_id: str, provider_domain_info: str | None = None) -> dict:
        return ensure_ok(self._r1.post("/sme/provider-registrations", json={
            "apfId": apf_id, "providerDomainInfo": provider_domain_info,
        }))

    def deregister_provider(self, apf_id: str) -> None:
        ensure_ok(self._r1.delete(f"/sme/provider-registrations/{apf_id}"))

    def publish_service(self, apf_id: str, service_name: str, producer_id: str, endpoint: str, version: str,
                         full_api_versions: list[str] | None = None, service_capabilities: dict | None = None,
                         selection_criteria: dict | None = None, module_scope: str = "", allowed_consumers: list[str] | None = None,
                         aef_profiles: list[dict] | None = None, api_supp_feats: str | None = None,
                         shareable_info: dict | None = None) -> dict:
        return ensure_ok(self._r1.post(f"/sme/published-apis/v1/{apf_id}/service-apis", json={
            "serviceName": service_name, "producerId": producer_id, "endpoint": endpoint, "version": version,
            "fullApiVersions": full_api_versions or [], "serviceCapabilities": service_capabilities or {},
            "selectionCriteria": selection_criteria or {}, "moduleScope": module_scope,
            "allowedConsumers": allowed_consumers or [], "aefProfiles": aef_profiles or [],
            "apiSuppFeats": api_supp_feats, "shareableInfo": shareable_info,
        }))

    def list_published_services(self, apf_id: str) -> list[dict]:
        return ensure_ok(self._r1.get(f"/sme/published-apis/v1/{apf_id}/service-apis"))

    def unpublish_service(self, apf_id: str, service_id: str) -> None:
        ensure_ok(self._r1.delete(f"/sme/published-apis/v1/{apf_id}/service-apis/{service_id}"))

    def discover_services(self, api_invoker_id: str | None = None, api_name: str | None = None,
                           api_version: str | None = None, aef_id: str | None = None, protocol: str | None = None,
                           data_format: str | None = None, comm_type: str | None = None) -> list[dict]:
        return ensure_ok(self._r1.get("/sme/service-apis/v1/allServiceAPIs", params={
            "api_invoker_id": api_invoker_id, "api_name": api_name, "api_version": api_version, "aef_id": aef_id,
            "protocol": protocol, "data_format": data_format, "comm_type": comm_type,
        }))

    def subscribe_to_events(self, subscriber_id: str, event_types: list[str], callback_uri: str,
                             api_ids: list[str] | None = None) -> dict:
        return ensure_ok(self._r1.post(f"/sme/capif-events/v1/{subscriber_id}/subscriptions", json={
            "subscriberId": subscriber_id, "eventTypes": event_types, "callbackUri": callback_uri, "apiIds": api_ids,
        }))

    def list_event_subscriptions(self, subscriber_id: str) -> list[dict]:
        return ensure_ok(self._r1.get(f"/sme/capif-events/v1/{subscriber_id}/subscriptions"))

    def unsubscribe_from_events(self, subscriber_id: str, subscription_id: str) -> None:
        ensure_ok(self._r1.delete(f"/sme/capif-events/v1/{subscriber_id}/subscriptions/{subscription_id}"))
