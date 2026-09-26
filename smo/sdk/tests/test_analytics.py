import uuid

import pytest

from smo_sdk._common import SdkError
from smo_sdk.analytics import AnalyticsClient


@pytest.fixture
def client(r1):
    return AnalyticsClient(r1)


def test_register_producer(client, r1):
    type_id = uuid.uuid4()
    client.register_producer("prod-1", "RAN.Coverage", [type_id], {"type": "object"})
    assert r1.calls[0] == {
        "verb": "post", "path": "/ran-analytics/producers",
        "params": {"producer_id": "prod-1", "analytics_type": "RAN.Coverage"},
        "files": None,
        "json": {"dme_input_types": [str(type_id)], "output_schema": {"type": "object"}},
    }


def test_list_producers(client, r1):
    client.list_producers(analytics_type="RAN.Coverage")
    assert r1.calls[0] == {
        "verb": "get", "path": "/ran-analytics/producers",
        "params": {"analytics_type": "RAN.Coverage", "producer_id": None},
    }


def test_publish_report(client, r1):
    source_id = uuid.uuid4()
    client.publish_report("RAN.Coverage", {"value": 1}, input_sources=[source_id], scope={"cell": "a"})
    call = r1.calls[0]
    assert call["verb"] == "post"
    assert call["path"] == "/mdaf/reports"
    assert call["params"] == {"analytics_type": "RAN.Coverage"}
    assert call["json"] == {"output": {"value": 1}, "input_sources": [str(source_id)], "scope": {"cell": "a"}}


def test_query_reports(client, r1):
    client.query_reports(analytics_type="RAN.Coverage")
    assert r1.calls[0] == {"verb": "get", "path": "/mdaf/reports", "params": {"analytics_type": "RAN.Coverage"}}


def test_subscribe_sends_scope_as_raw_unwrapped_body(client, r1):
    """subscribe's only body-eligible param is `scope` — the wire body is
    that dict directly, not {"scope": ...}; the other three args are
    plain strings, so they go in query params. Confirmed against MDAF's
    own OpenAPI schema before writing this."""
    client.subscribe("RAN.Coverage", "rapp-1", notification_destination="http://x/notify", scope={"cell": "a"})
    assert r1.calls[0] == {
        "verb": "post", "path": "/mdaf/subscriptions",
        "params": {"analytics_type": "RAN.Coverage", "requested_by": "rapp-1", "notification_destination": "http://x/notify"},
        "files": None,
        "json": {"cell": "a"},
    }


def test_unsubscribe(client, r1):
    sub_id = uuid.uuid4()
    client.unsubscribe(sub_id)
    assert r1.calls[0] == {"verb": "delete", "path": f"/mdaf/subscriptions/{sub_id}", "params": None}


def test_list_subscriptions(client, r1):
    client.list_subscriptions(requested_by="rapp-1")
    assert r1.calls[0] == {
        "verb": "get", "path": "/mdaf/subscriptions",
        "params": {"analytics_type": None, "requested_by": "rapp-1"},
    }


def test_raises_sdk_error_on_a_4xx_response(client, r1):
    r1.script(422, {"detail": "bad scope"})
    with pytest.raises(SdkError) as exc_info:
        client.subscribe("RAN.Coverage", "rapp-1", scope={"cell": "a"})
    assert exc_info.value.status_code == 422
    assert exc_info.value.body == {"detail": "bad scope"}
