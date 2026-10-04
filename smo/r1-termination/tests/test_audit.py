"""PR-SEC-11.3: the gateway records every authenticated change in the audit chain, and nothing else."""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from smo_shared import audit
from smo_shared.db import Base
from smo_shared.testing import make_test_engine

from app.main import ROUTES, app

client = TestClient(app)
AUTH = {"Authorization": "Bearer t"}
INTROSPECT_URL = f"{ROUTES['/sme']}/oauth2/introspect"


class FakeResponse:
    def __init__(self, content=b"{}", status_code=200):
        self.content, self.status_code, self.headers = content, status_code, {}

    def json(self):
        return json.loads(self.content)


@pytest.fixture(autouse=True)
def fresh_rate_limiter():
    from app.main import _limiter
    _limiter.clear()
    yield
    _limiter.clear()


@pytest.fixture
def gateway(monkeypatch):
    engine = make_test_engine()
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, future=True)
    monkeypatch.setattr("smo_shared.db.SessionLocal", sessions)
    monkeypatch.delenv("R1_AUDIT", raising=False)
    monkeypatch.delenv("SMO_ROLE_ENFORCEMENT", raising=False)
    state = {"sme_says": {"active": True, "client_id": "inv-1", "role": "rapp"}, "backend_status": 201, "sessions": sessions}

    class FakeAsyncClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def request(self, method, url, **kwargs):
            if url == INTROSPECT_URL:
                return FakeResponse(json.dumps(state["sme_says"]).encode())
            return FakeResponse(status_code=state["backend_status"])

    monkeypatch.setattr("app.main.httpx.AsyncClient", FakeAsyncClient)
    return state


def rows(gateway):
    with gateway["sessions"]() as db:
        return [(r.seq, r.actor, r.actor_role, r.action, r.target, r.result, r.detail) for r in db.query(audit.AuditEntry).order_by(audit.AuditEntry.seq)]


def test_a_change_is_recorded_with_its_caller_and_result(gateway):
    assert client.post("/dme/data-jobs", headers=AUTH, json={"secret": "never recorded"}).status_code == 201
    assert rows(gateway) == [(1, "inv-1", "rapp", "POST", "/dme/data-jobs", "201", None)]


def test_a_backend_refusal_is_recorded_with_its_status(gateway):
    gateway["backend_status"] = 409
    client.delete("/dme/data-jobs/j1", headers=AUTH)
    assert rows(gateway)[0][3:6] == ("DELETE", "/dme/data-jobs/j1", "409")


def test_a_role_refusal_is_recorded(gateway):
    assert client.post("/onboarding/packages", headers=AUTH).status_code == 403
    assert rows(gateway) == [(1, "inv-1", "rapp", "POST", "/onboarding/packages", "REFUSED:ROLE_NOT_PERMITTED", None)]


def test_an_audit_mode_pass_is_recorded_as_what_the_backend_said(gateway, monkeypatch):
    monkeypatch.setenv("SMO_ROLE_ENFORCEMENT", "audit")
    client.post("/onboarding/packages", headers=AUTH)
    assert rows(gateway)[0][5] == "201"


def test_reads_are_not_recorded(gateway):
    assert client.get("/dme/data-jobs", headers=AUTH).status_code == 201
    assert rows(gateway) == []


def test_an_unauthenticated_change_is_not_recorded(gateway):
    gateway["sme_says"] = {"active": False}
    assert client.post("/dme/data-jobs", headers=AUTH).status_code == 401
    assert client.post("/dme/data-jobs").status_code == 401
    assert rows(gateway) == []


def test_a_module_acting_for_an_rapp_is_recorded_as_the_module_with_the_rapp_in_the_detail(gateway):
    gateway["sme_says"] = {"active": True, "client_id": "dme-module", "role": "internal"}
    client.post("/ran-nf-oam/config-jobs", headers={**AUTH, "X-R1-On-Behalf-Of": "api-invoker-7"})
    assert rows(gateway) == [(1, "dme-module", "internal", "POST", "/ran-nf-oam/config-jobs", "201", {"onBehalfOf": "api-invoker-7"})]


def test_the_chain_of_the_calls_verifies(gateway):
    for path in ("/dme/data-jobs", "/dme/offers", "/onboarding/packages"):
        client.post(path, headers=AUTH)
    with gateway["sessions"]() as db:
        assert len(rows(gateway)) == 3 and audit.verify(db) is None


def test_the_switch_turns_it_off(gateway, monkeypatch):
    monkeypatch.setenv("R1_AUDIT", "off")
    client.post("/dme/data-jobs", headers=AUTH)
    assert rows(gateway) == []


def test_an_unreachable_database_does_not_fail_the_call(gateway, monkeypatch):
    monkeypatch.setattr("smo_shared.db.SessionLocal", lambda: (_ for _ in ()).throw(RuntimeError("database down")))
    assert client.post("/dme/data-jobs", headers=AUTH).status_code == 201
