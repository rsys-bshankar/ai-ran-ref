"""The exact edges of `normalise_base_url` and `forward_to_destination` (PR-GUI-8): what a changed comparison, constant or label would break. These two functions
are in the mutation scope of the shared library (`scripts/mutation_pilot.sh`), so each assertion names a mutant it kills."""

import httpx
import pytest

from smo_shared import webhook
from smo_shared.webhook import normalise_base_url

from test_webhook_forward import _call, _Recorder


def test_a_base_of_exactly_300_characters_is_accepted_and_one_more_is_not():
    head = "http://rapp:8000/"
    exact = head + "a" * (300 - len(head))
    assert len(exact) == 300 and normalise_base_url(exact) == exact
    assert normalise_base_url(exact + "a") is None


def test_the_first_character_that_is_allowed_and_the_last_that_is_not():
    assert normalise_base_url("http://rapp/!") == "http://rapp/!"          # 33
    assert normalise_base_url("http://rapp/~") == "http://rapp/~"          # 126
    assert normalise_base_url("http://rapp/ ") is None                     # 32
    assert normalise_base_url("http://rapp/\x7f") is None                  # 127
    assert normalise_base_url("http://rapp/\x1f") is None and normalise_base_url("http://rapp/\x00") is None


def test_whitespace_around_a_base_is_refused_even_when_it_is_not_ascii():
    assert normalise_base_url("http://rapp ") is None and normalise_base_url(" http://rapp") is None
    assert normalise_base_url("http://rapp") == "http://rapp"


@pytest.mark.parametrize("value", ["http://@rapp", "http://:pw@rapp", "http://u:@rapp", "http://rapp:0/x", "http://rapp/a/../b", "http://rapp/..", "http://rapp//",
                                   "http://rapp/a//b", "http://rapp/a?", "http://rapp/#", "http://rapp/a\\b", "http://rapp/a%41", "http://rapp:99999"])
def test_each_rule_refuses_on_its_own(value):
    assert normalise_base_url(value) is None


def test_a_letter_x_is_an_ordinary_character_in_a_base_and_only_slashes_are_trimmed():
    assert normalise_base_url("http://rapp/XX") == "http://rapp/XX"
    assert normalise_base_url("http://rapp/aX/") == "http://rapp/aX"
    assert normalise_base_url("http://rapp/Xa") == "http://rapp/Xa"


def test_a_port_other_than_zero_and_dots_that_are_not_traversal_are_fine():
    assert normalise_base_url("http://rapp:1") == "http://rapp:1"
    assert normalise_base_url("http://rapp:65535/v1.2/a.b") == "http://rapp:65535/v1.2/a.b"
    assert normalise_base_url("http://rapp/x/") == "http://rapp/x" and normalise_base_url("http://rapp") == "http://rapp"
    assert normalise_base_url(None) is None and normalise_base_url("") is None


@pytest.fixture
def counted(monkeypatch):
    calls = []
    monkeypatch.setattr(webhook.metrics, "record_outbound", lambda *args: calls.append(args))
    return calls


def test_a_forward_is_counted_by_outcome_with_the_duration_and_a_refusal_without_one(monkeypatch, counted):
    monkeypatch.setattr(webhook.httpx, "AsyncClient", _Recorder(httpx.Response(204)))
    assert _call(method="DELETE", destination="http://rapp:8000/x").status_code == 204
    monkeypatch.setattr(webhook.httpx, "AsyncClient", _Recorder(httpx.Response(503)))
    _call(method="Post", destination="http://rapp:8000/x")
    assert _call(method="GET", destination="http://127.0.0.1/x") is None
    assert [c[:4] for c in counted] == [("webhook", "operator-api", "delete", "2xx"), ("webhook", "operator-api", "post", "5xx"), ("webhook", "operator-api", "get", "blocked")]
    assert [len(c) for c in counted] == [5, 5, 4]
    assert all(isinstance(c[4], float) and 0 <= c[4] < 5 for c in counted[:2])


def test_a_timeout_and_an_error_are_counted_with_their_duration(monkeypatch, counted):
    monkeypatch.setattr(webhook.httpx, "AsyncClient", _Recorder(error=httpx.ReadTimeout("slow")))
    with pytest.raises(httpx.TimeoutException):
        _call(method="PUT", destination="http://rapp:8000/x")
    monkeypatch.setattr(webhook.httpx, "AsyncClient", _Recorder(error=httpx.ConnectError("down")))
    with pytest.raises(httpx.HTTPError):
        _call(method="PATCH", destination="http://rapp:8000/x")
    assert [c[:4] for c in counted] == [("webhook", "operator-api", "put", "timeout"), ("webhook", "operator-api", "patch", "error")]
    assert all(len(c) == 5 and isinstance(c[4], float) and 0 <= c[4] < 5 for c in counted)


def test_the_client_gets_the_timeout_and_the_mtls_arguments_for_the_destination(monkeypatch):
    recorder = _Recorder(httpx.Response(200))
    monkeypatch.setattr(webhook.httpx, "AsyncClient", recorder)
    monkeypatch.setattr(webhook.mtls, "webhook_kwargs", lambda destination: {"verify": f"context-for-{destination}"})
    _call(destination="https://rapp.internal:8443/x", timeout=7.5)
    assert recorder.calls[0][3] == {"timeout": 7.5, "verify": "context-for-https://rapp.internal:8443/x"}
