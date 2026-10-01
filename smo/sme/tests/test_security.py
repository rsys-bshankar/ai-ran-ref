"""OI-2-oauth2-scope, SA-SME-1-public-key and OI-5-sme-filters: scope
checking at token issuance, RFC 7523 client assertions verified with the
invoker's onboarded key, and invoker events with CAPIFEventFilter
matching. Run with: pytest smo/sme/tests -q
"""

import datetime
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from test_main import client, db_session_factory, register_body  # noqa: F401  (pytest fixtures)

from app.main import CLIENT_ASSERTION_TYPE, MAX_ASSERTION_LIFETIME_SECONDS, TOKEN_ENDPOINT_AUDIENCE


def _rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _pem(private_key) -> str:
    return private_key.public_key().public_bytes(serialization.Encoding.PEM,
                                                 serialization.PublicFormat.SubjectPublicKeyInfo).decode()


def _onboard(client, public_key="label-only"):
    resp = client.post("/invoker-registrations", json={"apiInvokerPublicKey": public_key})
    assert resp.status_code == 201, resp.text
    return resp.json()


def _secret_token(client, invoker, scope=None):
    return client.post("/oauth2/token", json={"grant_type": "client_credentials", "client_id": invoker["apiInvokerId"],
                                              "client_secret": invoker["onboardingSecret"], "scope": scope})


def _assertion(private_key, invoker_id, algorithm="RS256", **overrides):
    now = datetime.datetime.now(datetime.UTC)
    claims = {"iss": invoker_id, "sub": invoker_id, "aud": TOKEN_ENDPOINT_AUDIENCE, "jti": str(uuid.uuid4()),
              "iat": now, "exp": now + datetime.timedelta(seconds=60), **overrides}
    return jwt.encode(claims, private_key, algorithm=algorithm)


def _assertion_token(client, invoker_id, assertion, scope=None):
    return client.post("/oauth2/token", json={"grant_type": "client_credentials", "client_id": invoker_id,
                                              "client_assertion_type": CLIENT_ASSERTION_TYPE,
                                              "client_assertion": assertion, "scope": scope})


def _publish(client, name="kpi-api", aef_id="aef-1", producer="rapp-1", allowed=None):
    body = register_body(service_name=name, producer=producer,
                         aefProfiles=[{"aefId": aef_id, "protocol": "HTTP_1_1", "versions": []}])
    if allowed is not None:
        body["allowedConsumers"] = allowed
    resp = client.post(f"/published-apis/v1/{producer}/service-apis", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()["serviceId"]


# ---------------------------------------------------------------- OI-2-oauth2-scope

@pytest.mark.parametrize("scope", [None, "smo-internal", "smo-gui"])
def test_unscoped_and_internal_scopes_are_granted(client, scope):
    invoker = _onboard(client)
    resp = _secret_token(client, invoker, scope)
    assert resp.status_code == 200 and resp.json()["scope"] == scope


def test_a_capif_scope_naming_published_apis_is_granted_and_introspected(client):
    _publish(client, "kpi-api", "aef-1")
    _publish(client, "alarm-api", "aef-1", producer="rapp-2")
    _publish(client, "topo-api", "aef-2", producer="rapp-3")
    invoker = _onboard(client)
    scope = "3gpp#aef-1:kpi-api,alarm-api;aef-2:topo-api"

    token = _secret_token(client, invoker, scope)

    assert token.status_code == 200 and token.json()["scope"] == scope
    introspected = client.post("/oauth2/introspect", json={"token": token.json()["access_token"]}).json()
    assert introspected["active"] and introspected["scope"] == scope


@pytest.mark.parametrize("scope, reason", [
    ("3gpp#aef-1:no-such-api", "is not published"),
    ("3gpp#aef-9:kpi-api", "is not exposed by AEF"),
    ("3gpp#aef-1", "malformed scope entry"),
    ("3gpp#:kpi-api", "malformed scope entry"),
    ("read-everything", "scope must be"),
])
def test_a_scope_that_names_nothing_published_is_invalid(client, scope, reason):
    _publish(client, "kpi-api", "aef-1")
    resp = _secret_token(client, _onboard(client), scope)
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_scope" and reason in resp.json()["error_description"]


def test_a_scope_cannot_name_an_api_hidden_from_the_invoker(client):
    allowed = _onboard(client)
    _publish(client, "private-api", "aef-1", allowed=[allowed["apiInvokerId"]])
    assert _secret_token(client, allowed, "3gpp#aef-1:private-api").status_code == 200
    other = _secret_token(client, _onboard(client), "3gpp#aef-1:private-api")
    assert other.status_code == 400 and other.json()["error"] == "invalid_scope"


# ---------------------------------------------------------------- SA-SME-1-public-key

@pytest.mark.parametrize("key_factory, algorithm", [(_rsa_key, "RS256"), (lambda: ec.generate_private_key(ec.SECP256R1()), "ES256")])
def test_a_signed_client_assertion_authenticates_the_invoker(client, key_factory, algorithm):
    key = key_factory()
    invoker = _onboard(client, _pem(key))
    assert invoker["keyAuthentication"] is True

    resp = _assertion_token(client, invoker["apiInvokerId"], _assertion(key, invoker["apiInvokerId"], algorithm))

    assert resp.status_code == 200, resp.text
    introspected = client.post("/oauth2/introspect", json={"token": resp.json()["access_token"]}).json()
    assert introspected["client_id"] == invoker["apiInvokerId"]


def test_an_assertion_buys_one_token_only(client):
    key = _rsa_key()
    invoker = _onboard(client, _pem(key))
    assertion = _assertion(key, invoker["apiInvokerId"])
    assert _assertion_token(client, invoker["apiInvokerId"], assertion).status_code == 200
    replay = _assertion_token(client, invoker["apiInvokerId"], assertion)
    assert replay.status_code == 400 and "already used" in replay.json()["error_description"]


@pytest.mark.parametrize("overrides, signer, reason", [
    ({}, "other", "Signature verification failed"),
    ({"aud": "http://elsewhere/oauth2/token"}, "own", "Audience"),
    ({"iss": "someone-else"}, "own", "iss and sub"),
    ({"exp": datetime.datetime.now(datetime.UTC) - datetime.timedelta(seconds=5)}, "own", "expired"),
    ({"exp": datetime.datetime.now(datetime.UTC) + datetime.timedelta(seconds=MAX_ASSERTION_LIFETIME_SECONDS + 60)}, "own", "must expire within"),
    ({"jti": None}, "own", "jti"),
])
def test_a_bad_assertion_is_refused(client, overrides, signer, reason):
    key = _rsa_key()
    invoker = _onboard(client, _pem(key))
    claims = {k: v for k, v in overrides.items() if v is not None}
    assertion = _assertion(key if signer == "own" else _rsa_key(), invoker["apiInvokerId"], **claims)
    if overrides.get("jti", "keep") is None:
        assertion = jwt.encode({k: v for k, v in jwt.decode(assertion, options={"verify_signature": False}).items() if k != "jti"},
                               key, algorithm="RS256")
    resp = _assertion_token(client, invoker["apiInvokerId"], assertion)
    assert resp.status_code == 400 and resp.json()["error"] == "invalid_client"
    assert reason in resp.json()["error_description"]


def test_an_invoker_onboarded_with_a_label_cannot_use_an_assertion(client):
    invoker = _onboard(client, "smo-module:x")
    assert invoker["keyAuthentication"] is False
    resp = _assertion_token(client, invoker["apiInvokerId"], _assertion(_rsa_key(), invoker["apiInvokerId"]))
    assert resp.status_code == 400 and "no PEM public key" in resp.json()["error_description"]


def test_secret_and_assertion_together_is_an_invalid_request(client):
    key = _rsa_key()
    invoker = _onboard(client, _pem(key))
    resp = client.post("/oauth2/token", json={"grant_type": "client_credentials", "client_id": invoker["apiInvokerId"],
                                              "client_secret": invoker["onboardingSecret"],
                                              "client_assertion_type": CLIENT_ASSERTION_TYPE,
                                              "client_assertion": _assertion(key, invoker["apiInvokerId"])})
    assert resp.status_code == 400 and resp.json()["error"] == "invalid_request"


def test_a_malformed_pem_key_is_refused_at_onboarding(client):
    resp = client.post("/invoker-registrations", json={"apiInvokerPublicKey": "-----BEGIN PUBLIC KEY-----\nnot a key\n-----END PUBLIC KEY-----"})
    assert resp.status_code == 422 and resp.json()["detail"]["title"] == "SECURITY_CONTEXT_INVALID"


def test_key_rotation_stops_the_old_key_at_once(client):
    old, new = _rsa_key(), _rsa_key()
    invoker = _onboard(client, _pem(old))
    assert client.put(f"/invoker-registrations/{invoker['apiInvokerId']}", json={"apiInvokerPublicKey": _pem(new)}).status_code == 200
    assert _assertion_token(client, invoker["apiInvokerId"], _assertion(old, invoker["apiInvokerId"])).status_code == 400
    assert _assertion_token(client, invoker["apiInvokerId"], _assertion(new, invoker["apiInvokerId"])).status_code == 200
    assert client.put("/invoker-registrations/api-invoker-ghost", json={"apiInvokerPublicKey": "x"}).status_code == 400


def test_offboarding_revokes_the_invokers_tokens(client):
    invoker = _onboard(client)
    token = _secret_token(client, invoker).json()["access_token"]
    assert client.delete(f"/invoker-registrations/{invoker['apiInvokerId']}").status_code == 204
    assert client.post("/oauth2/introspect", json={"token": token}).json() == {"active": False}
    assert _secret_token(client, invoker).json()["error"] == "invalid_client"
    assert client.delete(f"/invoker-registrations/{invoker['apiInvokerId']}").status_code == 204  # idempotent


# ---------------------------------------------------------------- OI-5-sme-filters

@pytest.fixture
def webhooks(monkeypatch):
    calls = []
    monkeypatch.setattr("app.main.httpx.post", lambda url, json=None, timeout=None: calls.append((url, json)))
    return calls


def _subscribe(client, subscriber, events, **filters):
    resp = client.post(f"/capif-events/v1/{subscriber}/subscriptions", json={
        "subscriberId": subscriber, "eventTypes": events, "callbackUri": f"http://{subscriber}/cb", **filters})
    assert resp.status_code == 201, resp.text


def test_invoker_lifecycle_events_are_delivered(client, webhooks):
    _subscribe(client, "watcher", ["API_INVOKER_ONBOARDED", "API_INVOKER_UPDATED", "API_INVOKER_OFFBOARDED"])
    invoker = _onboard(client)["apiInvokerId"]
    client.put(f"/invoker-registrations/{invoker}", json={"apiInvokerPublicKey": "label-2"})
    client.delete(f"/invoker-registrations/{invoker}")
    assert [(body["eventType"], body["apiInvokerId"], body["eventDetail"]) for _, body in webhooks] == [
        (event, invoker, {"apiInvokerIds": [invoker]})
        for event in ("API_INVOKER_ONBOARDED", "API_INVOKER_UPDATED", "API_INVOKER_OFFBOARDED")]


def test_an_api_invoker_ids_filter_selects_one_invoker(client, webhooks):
    first = _onboard(client)["apiInvokerId"]
    _subscribe(client, "watcher", ["API_INVOKER_OFFBOARDED"], apiInvokerIds=[first])
    second = _onboard(client)["apiInvokerId"]
    client.delete(f"/invoker-registrations/{second}")
    client.delete(f"/invoker-registrations/{first}")
    assert [body["apiInvokerId"] for _, body in webhooks] == [first]


def test_an_aef_ids_filter_selects_services_exposed_by_that_aef(client, webhooks):
    _subscribe(client, "watcher", ["SERVICE_API_AVAILABLE"], aefIds=["aef-2"])
    _publish(client, "kpi-api", "aef-1")
    topo = _publish(client, "topo-api", "aef-2", producer="rapp-2")
    assert [(body["serviceId"], body["eventDetail"]["aefIds"]) for _, body in webhooks] == [(topo, ["aef-2"])]


def test_a_filter_of_another_kind_never_matches(client, webhooks):
    """An apiIds filter matches no invoker event; an apiInvokerIds filter
    matches no service event."""
    _subscribe(client, "a", ["API_INVOKER_ONBOARDED"], apiIds=["any"])
    _subscribe(client, "b", ["SERVICE_API_AVAILABLE"], apiInvokerIds=["any"])
    _onboard(client)
    _publish(client)
    assert webhooks == []


def test_subscriptions_list_their_filters(client):
    _subscribe(client, "watcher", ["SERVICE_API_UPDATE"], aefIds=["aef-1"], apiInvokerIds=None)
    [sub] = client.get("/capif-events/v1/watcher/subscriptions").json()["items"]
    assert (sub["aefIds"], sub["apiInvokerIds"], sub["apiIds"]) == (["aef-1"], None, None)


def test_unknown_event_types_are_refused(client):
    resp = client.post("/capif-events/v1/w/subscriptions", json={"subscriberId": "w", "eventTypes": ["API_INVOKER_EATEN"],
                                                                   "callbackUri": "http://w/cb"})
    assert resp.status_code == 422
