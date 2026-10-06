"""PR-SEC-9.3 (the optional bootstrap key) and PR-SEC-8.5 (the limiter's shared store) at the gateway."""

import importlib
import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import app.main as gateway
from app.main import app
from smo_shared import ratelimit
from smo_shared.roles import BOOTSTRAP_KEY_HEADER
from smo_shared.ratelimit import RateBucket, SharedTokenBuckets
from test_main import AUTH_HEADERS, _backend_that_accepts_any_token  # noqa: F401  (the fake backend the limit tests use)

client = TestClient(app)


# ---------------------------------------------------------------- the bootstrap key (PR-SEC-9.3)

def test_without_a_configured_key_bootstrap_is_open_and_a_sent_key_is_ignored():
    assert gateway.BOOTSTRAP_KEY is None                                    # the default
    assert client.get("/bootstrap").status_code == 200
    assert client.get("/bootstrap", headers={BOOTSTRAP_KEY_HEADER: "anything"}).status_code == 200


def test_with_a_key_bootstrap_needs_the_header_and_nothing_is_revealed_without_it(monkeypatch):
    monkeypatch.setattr(gateway, "BOOTSTRAP_KEY", "s3cret-key")
    refused = [client.get("/bootstrap"), client.get("/bootstrap", headers={BOOTSTRAP_KEY_HEADER: "wrong"}),
               client.get("/bootstrap", headers={BOOTSTRAP_KEY_HEADER: ""}), client.get("/bootstrap", headers={BOOTSTRAP_KEY_HEADER: "s3cret-key-and-more"})]
    for response in refused:
        assert response.status_code == 401 and response.json()["title"] == "UNAUTHORIZED"
        assert "apiEndpoints" not in response.text and "sme" not in response.text.replace("smo", "")     # no address leaks in the refusal
    ok = client.get("/bootstrap", headers={BOOTSTRAP_KEY_HEADER: "s3cret-key"})
    assert ok.status_code == 200 and {e["apiName"] for e in ok.json()["apiEndpoints"]} == {"service-apis", "published-apis"}


def test_the_key_is_compared_in_constant_time_and_a_non_ascii_header_is_just_wrong(monkeypatch):
    seen = []
    real = gateway.hmac.compare_digest
    monkeypatch.setattr(gateway.hmac, "compare_digest", lambda a, b: seen.append((a, b)) or real(a, b))
    monkeypatch.setattr(gateway, "BOOTSTRAP_KEY", "k")
    assert client.get("/bootstrap", headers={BOOTSTRAP_KEY_HEADER: "k"}).status_code == 200
    assert client.get("/bootstrap", headers={BOOTSTRAP_KEY_HEADER.lower(): "café".encode("latin-1")}).status_code == 401
    assert seen[0] == (b"k", b"k") and len(seen) == 2


def test_the_key_comes_from_the_environment_or_a_file_and_never_both(monkeypatch, tmp_path):
    monkeypatch.setenv("R1_BOOTSTRAP_KEY", "from-env")
    importlib.reload(gateway)
    try:
        assert gateway.BOOTSTRAP_KEY == "from-env"
        keyfile = tmp_path / "bootstrap_key"
        keyfile.write_text("from-file\n")
        monkeypatch.delenv("R1_BOOTSTRAP_KEY")
        monkeypatch.setenv("R1_BOOTSTRAP_KEY_FILE", str(keyfile))
        importlib.reload(gateway)
        assert gateway.BOOTSTRAP_KEY == "from-file"                          # the trailing newline of a secret file is not part of the key
        monkeypatch.setenv("R1_BOOTSTRAP_KEY", "also-env")
        with pytest.raises(RuntimeError, match="both R1_BOOTSTRAP_KEY and R1_BOOTSTRAP_KEY_FILE"):
            importlib.reload(gateway)
    finally:
        monkeypatch.undo()
        importlib.reload(gateway)


def test_the_declared_contract_has_an_optional_key_header_and_the_401():
    operation = app.openapi()["paths"]["/bootstrap"]["get"]
    header = next(p for p in operation["parameters"] if p["name"] == "x-bootstrap-key")
    assert header["in"] == "header" and header["required"] is False
    assert "401" in operation["responses"] and operation["security"] == []


# ---------------------------------------------------------------- the shared limiter (PR-SEC-8.5)

@pytest.fixture
def shared_limiter(monkeypatch, gateway_database):
    from sqlalchemy.orm import sessionmaker
    factory = sessionmaker(bind=gateway_database, autoflush=False, future=True)
    limiter = SharedTokenBuckets(lambda: float(gateway.os.environ.get("R1_RATE_PER_SECOND", "100")),
                                 lambda: float(gateway.os.environ.get("R1_RATE_BURST", "200")), session_factory=factory)
    monkeypatch.setattr(gateway, "_limiter", limiter)
    return limiter, factory


def test_the_default_store_is_the_in_process_limiter():
    assert gateway.RATE_STORE == "memory" and isinstance(gateway._limiter, ratelimit.TokenBuckets) and gateway._limiter.blocking is False


def test_postgres_store_builds_the_shared_limiter_and_a_bad_value_stops_the_service(monkeypatch):
    monkeypatch.setenv("R1_RATE_STORE", "postgres")
    try:
        importlib.reload(gateway)
        assert gateway.RATE_STORE == "postgres" and isinstance(gateway._limiter, SharedTokenBuckets)
        monkeypatch.setenv("R1_RATE_STORE", "memcached")
        with pytest.raises(RuntimeError, match="R1_RATE_STORE"):
            importlib.reload(gateway)
    finally:
        monkeypatch.undo()
        importlib.reload(gateway)


def test_with_the_shared_store_a_caller_over_its_budget_gets_429_and_the_budget_is_in_the_database(monkeypatch, shared_limiter):
    _, factory = shared_limiter
    monkeypatch.setenv("R1_RATE_BURST", "3")
    monkeypatch.setenv("R1_RATE_PER_SECOND", "0.001")
    forwarded: list = []
    _backend_that_accepts_any_token(monkeypatch, forwarded=forwarded)
    statuses = [client.get("/sme/service-apis/v1/allServiceAPIs", headers=AUTH_HEADERS).status_code for _ in range(5)]
    assert statuses == [200, 200, 200, 429, 429] and len(forwarded) == 3
    refused = client.get("/sme/x", headers=AUTH_HEADERS)
    assert refused.json()["title"] == "RATE_LIMITED" and int(refused.headers["Retry-After"]) >= 1
    with factory() as db:
        assert db.execute(select(RateBucket.caller)).scalars().all() == ["caller-a"]      # the bucket is the invoker id's row


def test_a_second_gateway_replica_over_the_same_database_sees_the_spent_budget(monkeypatch, shared_limiter):
    first, factory = shared_limiter
    second = SharedTokenBuckets(first._rate, first._burst, session_factory=factory)
    monkeypatch.setenv("R1_RATE_BURST", "2")
    monkeypatch.setenv("R1_RATE_PER_SECOND", "0.001")
    _backend_that_accepts_any_token(monkeypatch)
    assert [client.get("/sme/x", headers=AUTH_HEADERS).status_code for _ in range(2)] == [200, 200]       # replica one spends both tokens
    monkeypatch.setattr(gateway, "_limiter", second)                                                      # the next request lands on replica two
    assert client.get("/sme/x", headers=AUTH_HEADERS).status_code == 429


def test_a_database_error_does_not_refuse_the_request_and_is_logged(monkeypatch, caplog):
    def broken():
        raise ConnectionError("database down")
    monkeypatch.setattr(gateway, "_limiter", SharedTokenBuckets(lambda: 1.0, lambda: 2.0, session_factory=broken))
    monkeypatch.setenv("R1_RATE_BURST", "2")
    _backend_that_accepts_any_token(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="smo_shared.ratelimit"):
        statuses = [client.get("/sme/x", headers=AUTH_HEADERS).status_code for _ in range(3)]
    assert statuses == [200, 200, 429]                       # fail open to the replica's own bucket: bounded, not refused for the outage
    assert any("rate limiter store unavailable" in r.getMessage() for r in caplog.records)


def test_a_refused_unauthenticated_request_spends_nothing_in_the_shared_store(monkeypatch, shared_limiter):
    _, factory = shared_limiter
    _backend_that_accepts_any_token(monkeypatch)
    assert client.get("/sme/x").status_code == 401
    with factory() as db:
        assert db.execute(select(RateBucket)).first() is None
