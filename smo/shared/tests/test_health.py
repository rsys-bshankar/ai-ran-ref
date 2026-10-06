"""smo_shared.health — liveness and readiness probes (PR-ST-7). The database check also runs against real
Postgres when `SMO_TEST_POSTGRES_URL` is set (CI's `migration-postgres` job)."""

import os
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from smo_shared import db as smo_db
from smo_shared import r1_client
from smo_shared.health import database_check, install_health, run_checks, sme_token_check


def client(*checks) -> TestClient:
    app = FastAPI()
    install_health(app, checks=checks)
    return TestClient(app)


def passing():
    return None


def failing():
    raise ConnectionError("postgresql://smo:hunter2@db/smo refused")


def hanging():
    time.sleep(2)


def test_live_and_the_health_alias_answer_200_whatever_the_checks_say():
    c = client(failing)
    assert c.get("/live").json() == {"status": "live"}
    assert c.get("/health").json() == {"status": "healthy"}      # the alias existing callers use


def test_ready_is_200_when_every_check_passes():
    resp = client(passing).get("/ready")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ready", "checks": {"passing": "ok"}}


def test_ready_without_checks_is_200():
    assert client().get("/ready").json() == {"status": "ready", "checks": {}}


def test_a_failing_check_makes_ready_503_and_names_the_check_but_not_the_error_text():
    resp = client(passing, failing).get("/ready")
    assert resp.status_code == 503
    assert resp.json() == {"status": "not-ready", "checks": {"passing": "ok", "failing": "ConnectionError"}}
    assert "hunter2" not in resp.text                             # no connection string in a probe body


def test_a_hung_check_is_reported_as_a_timeout_instead_of_hanging_the_probe(monkeypatch):
    monkeypatch.setenv("READY_CHECK_TIMEOUT_SECONDS", "0.2")
    started = time.perf_counter()
    resp = client(hanging, passing).get("/ready")
    assert time.perf_counter() - started < 1.5
    assert resp.status_code == 503 and resp.json()["checks"] == {"hanging": "timeout", "passing": "ok"}


def test_checks_run_in_parallel():
    def slow_a():
        time.sleep(0.4)

    def slow_b():
        time.sleep(0.4)

    started = time.perf_counter()
    assert set(run_checks([slow_a, slow_b], timeout=3).values()) == {"ok"}
    assert time.perf_counter() - started < 0.75


# ---------------------------------------------------------------- the two shared checks

@pytest.fixture(params=["sqlite", "postgres"])
def database(request, tmp_path, monkeypatch):
    if request.param == "postgres":
        if not os.environ.get("SMO_TEST_POSTGRES_URL"):
            pytest.skip("SMO_TEST_POSTGRES_URL not set")
        url = os.environ["SMO_TEST_POSTGRES_URL"]
    else:
        url = f"sqlite:///{tmp_path / 'ready.db'}"
    engine = create_engine(url, future=True)
    monkeypatch.setattr(smo_db, "engine", engine)
    yield url
    engine.dispose()


def test_the_database_check_passes_on_a_reachable_database(database):
    database_check()
    assert client(database_check).get("/ready").status_code == 200


def test_a_down_database_makes_ready_503_while_live_stays_200(monkeypatch, tmp_path):
    monkeypatch.setattr(smo_db, "engine", create_engine(f"sqlite:///{tmp_path / 'no-such-dir' / 'x.db'}", future=True))
    c = client(database_check)
    assert c.get("/ready").status_code == 503
    assert c.get("/ready").json()["checks"] == {"database_check": "OperationalError"}
    assert c.get("/live").status_code == 200


def test_a_postgres_that_is_down_is_not_ready(monkeypatch):
    # nothing listens on this port: the same failure as a stopped database container
    monkeypatch.setattr(smo_db, "engine", create_engine(
        "postgresql+psycopg://smo:smo@127.0.0.1:1/smo", future=True, connect_args={"connect_timeout": 1}))
    resp = client(database_check).get("/ready")
    assert resp.status_code == 503 and resp.json()["checks"]["database_check"] == "OperationalError"


def test_the_sme_token_check_follows_whether_a_token_can_be_obtained(monkeypatch):
    monkeypatch.setattr(r1_client, "_module_token", lambda base_url, refresh=False: "tok")
    sme_token_check()
    monkeypatch.setattr(r1_client, "_module_token", lambda base_url, refresh=False: None)
    with pytest.raises(RuntimeError, match="no SMO access token"):
        sme_token_check()
    resp = client(sme_token_check).get("/ready")
    assert resp.status_code == 503 and resp.json()["checks"] == {"sme_token_check": "RuntimeError"}



def test_version_reports_the_build_the_image_set(monkeypatch):
    monkeypatch.setenv("MODULE", "aimgf")
    monkeypatch.setenv("SMO_VERSION", "1.4.0")
    monkeypatch.setenv("SMO_BUILD_SHA", "0123abcd")
    monkeypatch.setenv("SMO_BUILT_AT", "2026-10-06T08:00:00Z")
    resp = client(failing).get("/version")           # no readiness check is consulted
    assert resp.status_code == 200
    assert resp.json() == {"module": "aimgf", "version": "1.4.0", "buildSha": "0123abcd", "builtAt": "2026-10-06T08:00:00Z"}


def test_version_says_unknown_for_an_image_built_without_the_arguments(monkeypatch):
    for name in ("MODULE", "SMO_VERSION", "SMO_BUILD_SHA", "SMO_BUILT_AT"):
        monkeypatch.delenv(name, raising=False)
    body = client().get("/version").json()
    assert body == {"module": "fastapi", "version": "unknown", "buildSha": "unknown", "builtAt": "unknown"}
    monkeypatch.setenv("SMO_BUILD_SHA", "")           # an empty build argument is also "unknown"
    assert client().get("/version").json()["buildSha"] == "unknown"


def test_a_sample_rapps_module_name_drops_the_samples_prefix(monkeypatch):
    monkeypatch.setenv("MODULE", "samples/energy-saving-rapp")
    assert client().get("/version").json()["module"] == "energy-saving-rapp"
