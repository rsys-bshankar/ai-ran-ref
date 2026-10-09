"""Tests of `sdk.platform` (`PlatformClient`): that each SME method calls its route with the expected verb, path and body, and that an error status becomes `SdkError`. `execute_action` and `get_approval` are in `test_wave10_wrappers.py`.

Run with `cd sdk && PYTHONPATH=.:../shared python -m pytest tests/test_platform.py -q`; no network and no database. The `r1` fixture (`conftest.py`) is a recording fake of `R1Client`: every call is kept as `{verb, path, params, json, files}` and answered from `r1.script(...)` (default: 200 with `{}`).
"""

import pytest

from smo_sdk._common import SdkError
from smo_sdk.platform import PlatformClient


@pytest.fixture
def client(r1):
    """A `PlatformClient` over the recording fake."""
    return PlatformClient(r1)


def test_register_provider(client, r1):
    """`register_provider` posts the APF id and the provider domain info."""
    client.register_provider("apf-1", provider_domain_info="info")
    call = r1.calls[0]
    assert call["verb"] == "post"
    assert call["path"] == "/sme/provider-registrations"
    assert call["json"] == {"apfId": "apf-1", "providerDomainInfo": "info"}


def test_deregister_provider(client, r1):
    """`deregister_provider` deletes the registration by APF id."""
    client.deregister_provider("apf-1")
    assert r1.calls[0] == {"verb": "delete", "path": "/sme/provider-registrations/apf-1", "params": None}


def test_publish_service(client, r1):
    """`publish_service` posts the service with empty lists and dicts for the optional parts and `moduleScope` as given."""
    client.publish_service("apf-1", "svc-1", "producer-1", "http://x", "1.0", module_scope="scope-1")
    call = r1.calls[0]
    assert call["path"] == "/sme/published-apis/v1/apf-1/service-apis"
    assert call["json"] == {
        "serviceName": "svc-1", "producerId": "producer-1", "endpoint": "http://x", "version": "1.0",
        "fullApiVersions": [], "serviceCapabilities": {}, "selectionCriteria": {}, "moduleScope": "scope-1",
        "allowedConsumers": [], "aefProfiles": [], "apiSuppFeats": None, "shareableInfo": None,
    }


def test_list_published_services(client, r1):
    """`list_published_services` reads the APF's service APIs."""
    client.list_published_services("apf-1")
    assert r1.calls[0] == {"verb": "get", "path": "/sme/published-apis/v1/apf-1/service-apis", "params": None}


def test_unpublish_service(client, r1):
    """`unpublish_service` deletes one service API."""
    client.unpublish_service("apf-1", "svc-1")
    assert r1.calls[0] == {"verb": "delete", "path": "/sme/published-apis/v1/apf-1/service-apis/svc-1", "params": None}


def test_discover_services(client, r1):
    """`discover_services` sends all seven filters, the unset ones as null."""
    client.discover_services(api_name="my-api")
    assert r1.calls[0] == {
        "verb": "get", "path": "/sme/service-apis/v1/allServiceAPIs",
        "params": {
            "api_invoker_id": None, "api_name": "my-api", "api_version": None, "aef_id": None,
            "protocol": None, "data_format": None, "comm_type": None,
        },
    }


def test_subscribe_to_events(client, r1):
    """`subscribe_to_events` posts the event types and callback with the unset filters as null."""
    client.subscribe_to_events("sub-1", ["SERVICE_API_AVAILABLE"], "http://x/callback")
    call = r1.calls[0]
    assert call["path"] == "/sme/capif-events/v1/sub-1/subscriptions"
    assert call["json"] == {
        "subscriberId": "sub-1", "eventTypes": ["SERVICE_API_AVAILABLE"], "callbackUri": "http://x/callback", "apiIds": None,
        "apiInvokerIds": None, "aefIds": None,
    }


def test_subscribe_to_events_passes_every_capif_event_filter(client, r1):
    """The API id, invoker id and AEF id filters all reach the body."""
    client.subscribe_to_events("sub-1", ["API_INVOKER_ONBOARDED"], "http://x/callback", api_ids=["s1"],
                               api_invoker_ids=["inv-1"], aef_ids=["aef-1"])
    assert {k: r1.calls[0]["json"][k] for k in ("apiIds", "apiInvokerIds", "aefIds")} == {
        "apiIds": ["s1"], "apiInvokerIds": ["inv-1"], "aefIds": ["aef-1"]}


def test_list_event_subscriptions(client, r1):
    """`list_event_subscriptions` reads the subscriber's subscriptions."""
    client.list_event_subscriptions("sub-1")
    assert r1.calls[0] == {"verb": "get", "path": "/sme/capif-events/v1/sub-1/subscriptions", "params": None}


def test_unsubscribe_from_events(client, r1):
    """`unsubscribe_from_events` deletes one subscription of the subscriber."""
    client.unsubscribe_from_events("sub-1", "subscription-1")
    assert r1.calls[0] == {
        "verb": "delete", "path": "/sme/capif-events/v1/sub-1/subscriptions/subscription-1", "params": None,
    }


def test_raises_sdk_error_on_a_4xx_response(client, r1):
    """A 400 answer is raised as `SdkError` carrying the status code and the body."""
    r1.script(400, {"detail": "bad provider"})
    with pytest.raises(SdkError) as exc_info:
        client.register_provider("apf-1")
    assert exc_info.value.status_code == 400
    assert exc_info.value.body == {"detail": "bad provider"}
