"""PR-SEC-14: an instance's credentials are written to its Kubernetes Secret, replaced on rotation and deleted on teardown, and nowhere else.

Run with: cd smo/shared && PYTHONPATH=. python -m pytest tests/test_credential_delivery.py -q
"""

import httpx
import pytest

from smo_shared import credential_delivery as cd

ENV = {"RAPP_CREDENTIAL_DELIVERY": "kubernetes", "KUBERNETES_SERVICE_HOST": "10.0.0.1", "KUBERNETES_SERVICE_PORT": "443",
       "RAPP_K8S_NAMESPACE": "smo", "RAPP_K8S_TOKEN_FILE": "/unused", "RAPP_K8S_CA_FILE": "/unused"}


class FakeApi:
    """A Kubernetes API that keeps Secrets by (namespace, name)."""

    def __init__(self, fail_with=None):
        self.secrets, self.calls, self.fail_with = {}, [], fail_with

    def handler(self, request: httpx.Request) -> httpx.Response:
        import json
        self.calls.append((request.method, request.url.path, request.headers.get("authorization")))
        if self.fail_with is not None:
            return httpx.Response(self.fail_with, json={})
        parts = request.url.path.strip("/").split("/")                 # api v1 namespaces <ns> secrets [<name>]
        ns = parts[3]
        if request.method == "POST":
            body = json.loads(request.content)
            key = (ns, body["metadata"]["name"])
            if key in self.secrets:
                return httpx.Response(409, json={})
            self.secrets[key] = body
            return httpx.Response(201, json=body)
        key = (ns, parts[5])
        if request.method == "PUT":
            self.secrets[key] = json.loads(request.content)
            return httpx.Response(200, json={})
        if request.method == "DELETE":
            return httpx.Response(200 if self.secrets.pop(key, None) else 404, json={})
        return httpx.Response(405, json={})

    def factory(self, environ):
        return httpx.Client(base_url="https://k8s", transport=httpx.MockTransport(self.handler), headers={"Authorization": "Bearer tok"}), environ["RAPP_K8S_NAMESPACE"]


def test_the_default_is_off_and_does_nothing():
    """With delivery off (unset, or any unknown value) deliver returns None, makes no API call, and withdraw reports SKIPPED."""
    api = FakeApi()
    assert cd.mode({}) == "none" and cd.mode({"RAPP_CREDENTIAL_DELIVERY": "typo"}) == "none"
    assert cd.deliver("i1", "inv", "s3cret", environ={}, client_factory=api.factory) is None and api.calls == []
    assert cd.withdraw("i1", environ={}, client_factory=api.factory).startswith("SKIPPED")


def test_the_credentials_are_written_to_a_secret_named_for_the_instance():
    """deliver creates Secret `rapp-<instance>-credentials` holding exactly the three env keys, labelled as managed by rApp Management, using the
    bearer token.
    """
    api = FakeApi()
    assert cd.deliver("i1", "api-invoker-1", "s3cret", environ=ENV, client_factory=api.factory) == {"kubernetesSecret": "rapp-i1-credentials"}
    body = api.secrets[("smo", "rapp-i1-credentials")]
    assert body["stringData"] == {"SMO_INVOKER_ID": "api-invoker-1", "SMO_INVOKER_SECRET": "s3cret", "SMO_IDENTITY_KIND": "rapp"}
    assert body["metadata"]["labels"] == {"app.kubernetes.io/managed-by": "smo-rapp-mgmt", "smo/rapp-instance": "i1"}
    assert api.calls[0][2] == "Bearer tok"


def test_rotation_replaces_the_secret():
    """A second deliver for the same instance gets 409 from POST and replaces the Secret with PUT, so the new secret wins."""
    api = FakeApi()
    cd.deliver("i1", "inv-1", "first", environ=ENV, client_factory=api.factory)
    cd.deliver("i1", "inv-2", "second", environ=ENV, client_factory=api.factory)
    assert api.secrets[("smo", "rapp-i1-credentials")]["stringData"]["SMO_INVOKER_SECRET"] == "second"
    assert [m for m, *_ in api.calls] == ["POST", "POST", "PUT"]


def test_withdraw_deletes_it_and_a_missing_one_is_done():
    """withdraw deletes the Secret and treats an already-absent one (404) as DONE."""
    api = FakeApi()
    cd.deliver("i1", "inv", "s", environ=ENV, client_factory=api.factory)
    assert cd.withdraw("i1", environ=ENV, client_factory=api.factory) == "DONE" and api.secrets == {}
    assert cd.withdraw("i1", environ=ENV, client_factory=api.factory) == "DONE"


def test_a_refusal_by_the_api_server_is_a_failure_to_deliver_and_a_failed_withdraw_never_raises():
    """A 403 from the API server raises DeliveryFailed on deliver, while withdraw returns FAILED instead of raising so teardown continues."""
    api = FakeApi(fail_with=403)
    with pytest.raises(cd.DeliveryFailed, match="403"):
        cd.deliver("i1", "inv", "s", environ=ENV, client_factory=api.factory)
    assert cd.withdraw("i1", environ=ENV, client_factory=api.factory).startswith("FAILED")


def test_an_unreachable_api_server_is_a_failure_to_deliver():
    """A transport error raises DeliveryFailed ('could not be reached') on deliver and FAILED on withdraw."""
    def refuse(environ):
        return httpx.Client(base_url="https://k8s", transport=httpx.MockTransport(lambda r: (_ for _ in ()).throw(httpx.ConnectError("down")))), "smo"
    with pytest.raises(cd.DeliveryFailed, match="could not be reached"):
        cd.deliver("i1", "inv", "s", environ=ENV, client_factory=refuse)
    assert cd.withdraw("i1", environ=ENV, client_factory=refuse).startswith("FAILED")


def test_a_pod_without_kubernetes_access_says_what_is_missing(tmp_path):
    """Missing Kubernetes settings, or an unreadable token file, produce a DeliveryFailed that names the cause."""
    with pytest.raises(cd.DeliveryFailed, match="no Kubernetes access configured"):
        cd.deliver("i1", "inv", "s", environ={"RAPP_CREDENTIAL_DELIVERY": "kubernetes"})
    with pytest.raises(cd.DeliveryFailed, match="cannot read the service-account token"):
        cd._client({**ENV, "RAPP_K8S_TOKEN_FILE": str(tmp_path / "missing")})
