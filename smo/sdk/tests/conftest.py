"""Shared fake R1Client for sdk.* tests — records every call
(verb, path, params, json, files) and returns a scripted response,
the same "monkeypatch R1Client.<verb>" shape this build already uses
for e.g. nfo/tests/test_main.py's own FOCOM double.
"""

import pytest


class FakeResponse:
    def __init__(self, status_code=200, payload=None, content=b"", text=""):
        self.status_code = status_code
        self._payload = payload
        self.content = content or (b"{}" if payload is not None else b"")
        self.text = text

    def json(self):
        return self._payload


class RecordingR1Client:
    def __init__(self):
        self.calls = []
        self._next_response = FakeResponse(200, {})

    def script(self, status_code=200, payload=None, content=b"", text=""):
        self._next_response = FakeResponse(status_code, payload, content, text)

    def _record(self, verb, path, **kwargs):
        self.calls.append({"verb": verb, "path": path, **kwargs})
        return self._next_response

    def get(self, path, params=None, **kw):
        return self._record("get", path, params=params)

    def post(self, path, json=None, params=None, files=None, **kw):
        return self._record("post", path, json=json, params=params, files=files)

    def put(self, path, json=None, params=None, **kw):
        return self._record("put", path, json=json, params=params)

    def patch(self, path, json=None, params=None, **kw):
        return self._record("patch", path, json=json, params=params)

    def delete(self, path, params=None, **kw):
        return self._record("delete", path, params=params)


@pytest.fixture
def r1():
    return RecordingR1Client()
