"""smo_shared/webhook.py — the shared SSRF guard every module's
caller-registered notification/callback destination now routes through
(OPEN_ITEMS.md 6.3's PR #138 CodeQL py/full-ssrf finding). Run with:
cd smo/shared && PYTHONPATH=. python -m pytest tests -q
"""

import httpx
import pytest

from smo_shared.webhook import delete_webhook, get_webhook, is_safe_webhook_destination, post_webhook


@pytest.mark.parametrize("destination", [
    "http://consumer/callback",
    "http://rapp-1:8000/cb",
    "https://operator:8000/dispatch",
    "http://10.0.3.7/cb",       # private-range IP — a real deployment's own container
    "http://172.18.0.5/cb",     # Docker's default bridge range
])
def test_allows_ordinary_http_and_https_destinations(destination):
    assert is_safe_webhook_destination(destination) is True


@pytest.mark.parametrize("destination", [
    None,
    "",
    "file:///etc/passwd",
    "gopher://internal/x",
    "data:text/plain,hi",
    "ftp://consumer/cb",
    "http://127.0.0.1/cb",
    "http://127.0.0.1:8000/cb",
    "http://[::1]/cb",
    "http://169.254.169.254/latest/meta-data/",   # cloud metadata endpoint
    "http://169.254.1.1/cb",                      # link-local, same range as metadata
    "http://localhost/cb",
    "http://LOCALHOST/cb",
    "http://metadata.google.internal/computeMetadata/v1/",
    "http://0.0.0.0/cb",
    "http://224.0.0.1/cb",   # multicast
    "not-a-url",
])
def test_rejects_dangerous_or_malformed_destinations(destination):
    assert is_safe_webhook_destination(destination) is False


def test_post_webhook_calls_httpx_post_for_an_allowed_destination(monkeypatch):
    calls = []
    monkeypatch.setattr("smo_shared.webhook.httpx.post",
                         lambda url, json=None, timeout=None: calls.append((url, json)) or httpx.Response(200))
    resp = post_webhook("http://consumer/cb", json={"a": 1}, timeout=2.0)
    assert calls == [("http://consumer/cb", {"a": 1})]
    assert resp is not None and resp.status_code == 200


def test_post_webhook_no_ops_for_a_disallowed_destination(monkeypatch):
    calls = []
    monkeypatch.setattr("smo_shared.webhook.httpx.post", lambda url, json=None, timeout=None: calls.append(url))
    resp = post_webhook("http://169.254.169.254/latest/meta-data/", json={"a": 1})
    assert calls == []
    assert resp is None


def test_post_webhook_swallows_an_unreachable_destination(monkeypatch):
    def raise_error(url, json=None, timeout=None):
        raise httpx.ConnectError("boom")
    monkeypatch.setattr("smo_shared.webhook.httpx.post", raise_error)
    assert post_webhook("http://consumer/cb", json={}) is None


def test_get_webhook_calls_httpx_get_for_an_allowed_destination(monkeypatch):
    monkeypatch.setattr("smo_shared.webhook.httpx.get", lambda url, timeout=None: httpx.Response(200))
    assert get_webhook("http://producer/health") is not None


def test_get_webhook_no_ops_for_a_disallowed_destination(monkeypatch):
    calls = []
    monkeypatch.setattr("smo_shared.webhook.httpx.get", lambda url, timeout=None: calls.append(url))
    assert get_webhook("http://127.0.0.1/health") is None
    assert calls == []


def test_delete_webhook_calls_httpx_delete_for_an_allowed_destination(monkeypatch):
    calls = []
    monkeypatch.setattr("smo_shared.webhook.httpx.delete",
                         lambda url, timeout=None: calls.append(url) or httpx.Response(204))
    resp = delete_webhook("http://producer/jobs/123")
    assert calls == ["http://producer/jobs/123"]
    assert resp is not None and resp.status_code == 204


def test_delete_webhook_no_ops_for_a_disallowed_destination(monkeypatch):
    calls = []
    monkeypatch.setattr("smo_shared.webhook.httpx.delete", lambda url, timeout=None: calls.append(url))
    assert delete_webhook("file:///etc/passwd") is None
    assert calls == []
