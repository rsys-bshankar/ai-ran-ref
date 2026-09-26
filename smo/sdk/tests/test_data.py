import uuid

import pytest

from smo_sdk._common import SdkError
from smo_sdk.data import DataClient


@pytest.fixture
def client(r1):
    return DataClient(r1)


def test_register_type(client, r1):
    client.register_type("RAN", "CoverageIssue", "1.0", "RAN.CoverageIssue", "rapp-1", {"type": "object"},
                          "http://x/health", "http://x/jobs", collection_spec={"a": 1})
    call = r1.calls[0]
    assert call == {"verb": "post", "path": "/dme/production-capabilities", "params": None, "files": None, "json": {
        "namespace": "RAN", "name": "CoverageIssue", "version": "1.0", "typeName": "RAN.CoverageIssue",
        "producerId": "rapp-1", "dataProductionSchema": {"type": "object"}, "collectionSpec": {"a": 1},
        "producerHealthCallbackUrl": "http://x/health", "jobCallbackUrl": "http://x/jobs",
    }}


def test_discover_types(client, r1):
    r1.script(200, [{"dmeTypeId": "x"}])
    result = client.discover_types(data_category="RAN")
    assert r1.calls[0] == {"verb": "get", "path": "/dme/dme-types", "params": {"data_category": "RAN"}}
    assert result == [{"dmeTypeId": "x"}]


def test_deregister_producer(client, r1):
    client.deregister_producer("rapp-1")
    assert r1.calls[0] == {"verb": "delete", "path": "/dme/production-capabilities", "params": {"producer_id": "rapp-1"}}


def test_query_producer_status(client, r1):
    client.query_producer_status("rapp-1")
    assert r1.calls[0]["path"] == "/dme/production-capabilities/rapp-1/status"


def test_create_data_job(client, r1):
    dme_type_id = uuid.uuid4()
    client.create_data_job(dme_type_id, "ONE_TIME", "PULL_HTTP", "consumer-1", production_job_definition={"a": 1})
    call = r1.calls[0]
    assert call["path"] == "/dme/data-jobs"
    assert call["json"] == {
        "dataDeliveryMode": "ONE_TIME", "dmeTypeId": str(dme_type_id), "productionJobDefinition": {"a": 1},
        "dataDeliveryMethod": "PULL_HTTP", "deliveryDetails": {}, "consumerId": "consumer-1",
    }


def test_get_data_job(client, r1):
    job_id = uuid.uuid4()
    client.get_data_job(job_id)
    assert r1.calls[0] == {"verb": "get", "path": f"/dme/data-jobs/{job_id}", "params": None}


def test_update_data_job(client, r1):
    job_id, dme_type_id = uuid.uuid4(), uuid.uuid4()
    client.update_data_job(job_id, dme_type_id, "ONE_TIME", "PULL_HTTP", "consumer-1")
    assert r1.calls[0]["verb"] == "put"
    assert r1.calls[0]["path"] == f"/dme/data-jobs/{job_id}"


def test_query_data_job_status(client, r1):
    job_id = uuid.uuid4()
    client.query_data_job_status(job_id)
    assert r1.calls[0]["path"] == f"/dme/data-jobs/{job_id}/status"


def test_terminate_data_job(client, r1):
    job_id = uuid.uuid4()
    client.terminate_data_job(job_id)
    assert r1.calls[0] == {"verb": "delete", "path": f"/dme/data-jobs/{job_id}", "params": None}


def test_list_data_jobs(client, r1):
    client.list_data_jobs(consumer_id="consumer-1")
    assert r1.calls[0] == {"verb": "get", "path": "/dme/data-jobs", "params": {"dme_type_id": None, "consumer_id": "consumer-1"}}


def test_create_data_offer(client, r1):
    dme_type_id = uuid.uuid4()
    client.create_data_offer(dme_type_id, "ONE_TIME", ["PULL_HTTP"], "http://x/terminate")
    call = r1.calls[0]
    assert call["path"] == "/dme/offers"
    assert call["json"]["dataDeliveryMethods"] == ["PULL_HTTP"]
    assert call["json"]["dataOfferTerminationNotificationUri"] == "http://x/terminate"


def test_get_and_terminate_data_offer(client, r1):
    offer_id = uuid.uuid4()
    client.get_data_offer(offer_id)
    client.terminate_data_offer(offer_id)
    assert r1.calls[0] == {"verb": "get", "path": f"/dme/offers/{offer_id}", "params": None}
    assert r1.calls[1] == {"verb": "delete", "path": f"/dme/offers/{offer_id}", "params": None}


def test_list_data_offers(client, r1):
    client.list_data_offers()
    assert r1.calls[0] == {"verb": "get", "path": "/dme/offers", "params": {"dme_type_id": None}}


def test_notify_data_available_sends_raw_unwrapped_body(client, r1):
    """offer_data_availability's body param is a single plain `dict` —
    the wire body is that dict directly, not {"payload": ...}. Confirmed
    against the route's own OpenAPI schema before writing this."""
    offer_id = uuid.uuid4()
    client.notify_data_available(offer_id, {"ready": True})
    assert r1.calls[0] == {"verb": "post", "path": f"/dme/offers/{offer_id}/notify", "params": None, "files": None, "json": {"ready": True}}


def test_subscribe_type_changes(client, r1):
    client.subscribe_type_changes("http://x/notify", "owner-1")
    assert r1.calls[0]["json"] == {"notificationDestination": "http://x/notify", "owner": "owner-1"}


def test_list_and_get_and_unsubscribe_type_changes(client, r1):
    sub_id = uuid.uuid4()
    client.list_type_subscriptions(owner="owner-1")
    client.get_type_subscription(sub_id)
    client.unsubscribe_type_changes(sub_id)
    assert r1.calls[0] == {"verb": "get", "path": "/dme/type-subscriptions", "params": {"owner": "owner-1"}}
    assert r1.calls[1] == {"verb": "get", "path": f"/dme/type-subscriptions/{sub_id}", "params": None}
    assert r1.calls[2] == {"verb": "delete", "path": f"/dme/type-subscriptions/{sub_id}", "params": None}


def test_raises_sdk_error_on_a_4xx_response(client, r1):
    r1.script(404, {"detail": "no such data job"})
    with pytest.raises(SdkError) as exc_info:
        client.get_data_job(uuid.uuid4())
    assert exc_info.value.status_code == 404
    assert exc_info.value.body == {"detail": "no such data job"}
