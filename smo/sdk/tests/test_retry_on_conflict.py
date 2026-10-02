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
        self.responses, self.calls = list(responses), []

    def _answer(self, verb, path, **kw):
        self.calls.append((verb, path))
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
