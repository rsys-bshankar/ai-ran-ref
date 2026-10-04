"""Every service answers /live, /ready and the /health alias (PR-ST-7), and compose probes /ready."""

from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from smo_shared import db as smo_db
from smo_shared import r1_client

SMO_ROOT = Path(__file__).resolve().parent.parent
# services whose readiness includes the database; the rest (the gateway, the SME-only caller, the mocks, the
# sample rApps) keep no database of their own
WITHOUT_DATABASE = {"r1-termination", "mllf", "mock-near-rt-ric", "mock-o1-adaptor", "energy-saving-rapp",
                    "mobility-optimization-rapp", "coverage-optimization-rapp", "traffic-steering-rapp"}


@pytest.fixture
def healthy(monkeypatch, shared_engine):
    monkeypatch.setattr(smo_db, "engine", shared_engine)
    monkeypatch.setattr(r1_client, "_module_token", lambda base_url, refresh=False: "tok")


def test_every_service_is_live_and_ready_when_its_dependencies_answer(loaded_apps, healthy):
    for name, main in loaded_apps.items():
        client = TestClient(main.app)
        assert client.get("/live").status_code == 200, name
        assert client.get("/health").status_code == 200, name
        ready = client.get("/ready")
        assert ready.status_code == 200 and ready.json()["status"] == "ready", name


def test_a_down_database_takes_every_database_service_out_of_rotation_but_never_fails_liveness(
        loaded_apps, healthy, monkeypatch, tmp_path):
    monkeypatch.setattr(smo_db, "engine", create_engine(f"sqlite:///{tmp_path / 'missing' / 'x.db'}", future=True))
    for name, main in loaded_apps.items():
        client = TestClient(main.app)
        assert client.get("/live").status_code == 200, name
        assert client.get("/health").status_code == 200, name
        ready = client.get("/ready")
        if name in WITHOUT_DATABASE:
            assert ready.status_code == 200, name
        else:
            assert ready.status_code == 503 and ready.json()["checks"]["database_check"] == "OperationalError", name


def test_a_module_that_cannot_get_a_token_from_sme_is_not_ready(loaded_apps, healthy, monkeypatch):
    monkeypatch.setattr(r1_client, "_module_token", lambda base_url, refresh=False: None)
    not_ready = {name for name, main in loaded_apps.items() if TestClient(main.app).get("/ready").status_code == 503}
    # callers of R1 are not ready without a token; SME itself (the issuer), the gateway and focom never ask for one
    assert {"nfo", "aimgf", "rapp-mgmt", "mllf"} <= not_ready
    assert not_ready.isdisjoint({"sme", "focom", "r1-termination", "mock-near-rt-ric", "mock-o1-adaptor"})


def test_the_gateway_probes_need_no_token(loaded_apps):
    spec = loaded_apps["r1-termination"].app.openapi()
    for path in ("/health", "/live", "/ready"):
        assert all(op["security"] == [] for op in spec["paths"][path].values()), path


def test_compose_probes_ready_on_every_service_built_from_the_shared_dockerfile():
    services = yaml.safe_load((SMO_ROOT / "docker-compose.yml").read_text())["services"]
    built = {name for name, svc in services.items()
             if isinstance(svc.get("build"), dict) and svc["build"].get("context") == "."
             and name not in ("gui-bff", "migrate")}      # the BFF is not on smo_shared; migrate is a one-shot, not a server
    assert len(built) >= 20
    workers = {name for name in built if "smo_shared.worker" in " ".join(services[name].get("command") or [])}
    assert workers == {"ran-nf-oam-worker"}                  # PR-MSG-4: a worker has no port, so no /ready
    for name in built - workers:
        probe = services[name]["healthcheck"]["test"]
        assert "/ready" in " ".join(probe), name
    for name in workers:                                     # it is healthy while it keeps touching its heartbeat file
        probe = " ".join(services[name]["healthcheck"]["test"])
        assert "/tmp/worker-heartbeat" in probe and "/ready" not in probe, name
    assert "healthcheck" not in services["gui-bff"]
    assert "healthcheck" not in services["migrate"] and services["migrate"]["restart"] == "no"   # runs to completion and exits
