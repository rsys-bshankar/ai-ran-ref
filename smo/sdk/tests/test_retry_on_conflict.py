"""The SDK repeats a mutating call once when the platform answers 409 CONCURRENT_MODIFICATION (PR-ST-2)."""

from smo_sdk._common import SdkError, ensure_ok
from smo_sdk.platform import PlatformClient
import pytest
from conftest import FakeResponse


def _conflict():
    return FakeResponse(409, {"detail": {"title": "CONCURRENT_MODIFICATION", "status": 409, "detail": "repeat"}})


class Scripted:
    """An R1 client that answers each call from a list, and records the calls."""

    def __init__(self, *responses):
        self.responses, self.calls, self.headers = list(responses), [], []

    def _answer(self, verb, path, **kw):
        self.calls.append((verb, path))
        self.headers.append(kw.get("headers"))
        return self.responses.pop(0)

    def get(self, path, **kw):
        return self._answer("get", path, **kw)

    def delete(self, path, **kw):
        return self._answer("delete", path, **kw)

    def post(self, path, **kw):
        return self._answer("post", path, **kw)


def test_a_lost_write_race_is_repeated_once_and_the_second_answer_is_returned():
    r1 = Scripted(_conflict(), FakeResponse(204))
    PlatformClient(r1).deregister_provider("apf-1")
    assert r1.calls == [("delete", "/sme/provider-registrations/apf-1")] * 2


def test_a_second_conflict_is_raised_not_retried_again():
    r1 = Scripted(_conflict(), _conflict(), FakeResponse(204))
    with pytest.raises(SdkError) as err:
        PlatformClient(r1).deregister_provider("apf-1")
    assert err.value.status_code == 409
    assert len(r1.calls) == 2


@pytest.mark.parametrize("body", [
    {"detail": {"title": "LIFECYCLE_ILLEGAL_TRANSITION", "status": 409}},
    {"detail": {"title": "SERVICE_NAME_CONFLICT", "status": 409}},
    {"detail": "plain string"},
    None,
])
def test_any_other_409_is_final(body):
    r1 = Scripted(FakeResponse(409, body))
    with pytest.raises(SdkError):
        PlatformClient(r1).deregister_provider("apf-1")
    assert len(r1.calls) == 1


def test_reads_are_never_retried():
    r1 = Scripted(_conflict())
    with pytest.raises(SdkError):
        ensure_ok(PlatformClient(r1)._r1.get("/anything"))
    assert len(r1.calls) == 1


def test_a_call_that_uploads_files_is_not_repeated():
    r1 = Scripted(_conflict(), FakeResponse(200, {}))
    resp = PlatformClient(r1)._r1.post("/upload", files={"f": b"x"})
    assert resp.status_code == 409 and len(r1.calls) == 1


def test_a_successful_call_is_sent_once():
    r1 = Scripted(FakeResponse(204))
    PlatformClient(r1).deregister_provider("apf-1")
    assert len(r1.calls) == 1


def test_every_post_carries_an_idempotency_key_and_the_repeat_reuses_it():
    r1 = Scripted(_conflict(), FakeResponse(201, {}))
    PlatformClient(r1)._r1.post("/nfo/deployments", json={})
    keys = [h["Idempotency-Key"] for h in r1.headers]
    assert len(keys) == 2 and keys[0] == keys[1] and len(keys[0]) == 32


def test_each_post_gets_its_own_key():
    r1 = Scripted(FakeResponse(201, {}), FakeResponse(201, {}))
    client = PlatformClient(r1)._r1
    client.post("/a", json={})
    client.post("/a", json={})
    assert r1.headers[0]["Idempotency-Key"] != r1.headers[1]["Idempotency-Key"]


def test_a_callers_own_key_is_kept_and_other_verbs_and_uploads_get_none():
    r1 = Scripted(FakeResponse(201, {}), FakeResponse(204), FakeResponse(200, {}))
    client = PlatformClient(r1)._r1
    client.post("/a", json={}, headers={"Idempotency-Key": "mine"})
    client.delete("/a")
    client.post("/upload", files={"f": b"x"})
    assert r1.headers[0] == {"Idempotency-Key": "mine"}
    assert r1.headers[1] is None and r1.headers[2] is None
