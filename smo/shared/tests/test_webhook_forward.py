"""smo_shared/webhook.py: the base URL a workload registers (`normalise_base_url`) and the forwarded call the gateway makes to it
(`forward_to_destination`), PR-GUI-8."""

import asyncio

import httpx
import pytest

from smo_shared import webhook
from smo_shared.webhook import forward_to_destination, normalise_base_url


@pytest.mark.parametrize("value,expected", [
    ("http://rapp:8000", "http://rapp:8000"),
    ("http://rapp:8000/", "http://rapp:8000"),
    ("https://rapp.example:8443/operator/", "https://rapp.example:8443/operator"),
    ("http://10.0.3.7:8000", "http://10.0.3.7:8000"),
])
def test_a_plain_base_is_accepted_and_loses_its_trailing_slash(value, expected):
    assert normalise_base_url(value) == expected


@pytest.mark.parametrize("value", [
    None, "", " http://rapp", "http://rapp ", "http://ra pp", "http://rapp\n", "ftp://rapp", "file:///x", "//rapp", "rapp:8000",
    "http://127.0.0.1", "http://localhost", "http://169.254.169.254", "http://[::1]:80", "http://metadata.google.internal",
    "http://u:p@rapp", "http://u@rapp", "http://rapp?x=1", "http://rapp#f", "http://rapp/a?", "http://rapp/../a", "http://rapp//a",
    "http://rapp/%2e", "http://rapp\\a", "http://rapp:0", "http://rapp:99999", "http://" + "a" * 300,
])
def test_an_unsafe_or_malformed_base_is_refused(value):
    assert normalise_base_url(value) is None


class _Recorder:
    def __init__(self, response=None, error=None):
        self.calls, self.response, self.error = [], response, error

    def __call__(self, **client_kwargs):
        recorder = self

        class Client:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return False

            async def request(self, method, url, **kw):
                recorder.calls.append((method, url, kw, client_kwargs))
                if recorder.error:
                    raise recorder.error
                return recorder.response

        return Client()


def _call(**kw):
    defaults = dict(headers={"a": "b"}, params=[("q", "1")], content=b"{}", timeout=3.0)
    return asyncio.run(forward_to_destination(kw.pop("method", "POST"), kw.pop("destination"), **{**defaults, **kw}))


def test_a_forwarded_call_carries_method_headers_query_and_body(monkeypatch):
    recorder = _Recorder(httpx.Response(204))
    monkeypatch.setattr(webhook.httpx, "AsyncClient", recorder)
    answer = _call(destination="http://rapp:8000/instances/x")
    assert answer.status_code == 204
    method, url, kw, client_kwargs = recorder.calls[0]
    assert (method, url) == ("POST", "http://rapp:8000/instances/x")
    assert kw == {"headers": {"a": "b"}, "params": [("q", "1")], "content": b"{}"} and client_kwargs["timeout"] == 3.0


@pytest.mark.parametrize("destination", ["http://127.0.0.1:8000/x", "http://169.254.169.254/x", "file:///etc/passwd", None, ""])
def test_a_forbidden_destination_is_not_called(monkeypatch, destination):
    recorder = _Recorder(httpx.Response(200))
    monkeypatch.setattr(webhook.httpx, "AsyncClient", recorder)
    assert _call(destination=destination) is None
    assert recorder.calls == []


def test_a_timeout_and_a_transport_error_propagate_as_such(monkeypatch):
    monkeypatch.setattr(webhook.httpx, "AsyncClient", _Recorder(error=httpx.ReadTimeout("secret detail")))
    with pytest.raises(httpx.TimeoutException):
        _call(destination="http://rapp:8000/x")
    monkeypatch.setattr(webhook.httpx, "AsyncClient", _Recorder(error=httpx.ConnectError("secret detail")))
    with pytest.raises(httpx.HTTPError):
        _call(destination="http://rapp:8000/x")
