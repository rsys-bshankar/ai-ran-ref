"""PR-OBS-2: request count and latency series, and /metrics."""

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from prometheus_client import REGISTRY

from smo_shared.health import install_health
from smo_shared.metrics import install_metrics


def _app() -> FastAPI:
    app = FastAPI()
    install_metrics(app)
    install_health(app)

    @app.get("/models/{model_id}")
    def model(model_id: str):
        if model_id == "boom":
            raise HTTPException(status_code=409, detail="x")
        return {"id": model_id}

    @app.get("/crash")
    def crash():
        raise RuntimeError("no")

    return app


def _sample(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_a_request_is_counted_under_its_route_template_and_status():
    client = TestClient(_app())
    before = _sample("smo_http_requests_total", method="GET", route="/models/{model_id}", status="200")
    client.get("/models/a")
    client.get("/models/b")
    assert _sample("smo_http_requests_total", method="GET", route="/models/{model_id}", status="200") == before + 2


def test_raw_ids_never_become_label_values():
    TestClient(_app()).get("/models/some-unique-id-123")
    assert "some-unique-id-123" not in TestClient(_app()).get("/metrics").text


def test_error_statuses_have_their_own_series_and_unmatched_paths_share_one():
    client = TestClient(_app(), raise_server_exceptions=False)
    b409 = _sample("smo_http_requests_total", method="GET", route="/models/{model_id}", status="409")
    b404 = _sample("smo_http_requests_total", method="GET", route="unmatched", status="404")
    b500 = _sample("smo_http_requests_total", method="GET", route="/crash", status="500")
    client.get("/models/boom")
    client.get("/nope/1")
    client.get("/nope/2")
    client.get("/crash")
    assert _sample("smo_http_requests_total", method="GET", route="/models/{model_id}", status="409") == b409 + 1
    assert _sample("smo_http_requests_total", method="GET", route="unmatched", status="404") == b404 + 2
    assert _sample("smo_http_requests_total", method="GET", route="/crash", status="500") == b500 + 1


def test_latency_is_observed_in_a_histogram():
    client = TestClient(_app())
    before = _sample("smo_http_request_duration_seconds_count", method="GET", route="/models/{model_id}", status="200")
    client.get("/models/a")
    assert _sample("smo_http_request_duration_seconds_count", method="GET", route="/models/{model_id}", status="200") == before + 1
    assert _sample("smo_http_request_duration_seconds_sum", method="GET", route="/models/{model_id}", status="200") > 0


def test_probes_and_the_scrape_itself_are_not_counted():
    client = TestClient(_app())
    client.get("/live")
    client.get("/health")
    client.get("/metrics")
    client.get("/metrics")
    text = client.get("/metrics").text
    for route in ("/live", "/health", "/metrics"):
        assert f'route="{route}"' not in text


def test_metrics_is_prometheus_text_and_not_in_the_openapi_spec():
    client = TestClient(_app())
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "# TYPE smo_http_requests_total counter" in response.text
    assert "/metrics" not in client.get("/openapi.json").json()["paths"]


# --- PR-OBS-2.5: state machine transitions ----------------------------------------------------------------------------------------

def test_every_transition_is_counted_by_machine_state_event_and_target_and_refusals_have_their_own_series():
    import enum
    from smo_shared.statemachine import IllegalTransition, StateMachine

    class DemoState(enum.Enum):
        NEW = "NEW"
        DONE = "DONE"

    class DemoEvent(enum.Enum):
        FINISH = "FINISH"
        REOPEN = "REOPEN"

    fsm = StateMachine().add(DemoState.NEW, DemoEvent.FINISH, DemoState.DONE)
    labels = dict(machine="DemoState", from_state="NEW", event="FINISH", to_state="DONE")
    before = _sample("smo_fsm_transitions_total", **labels)
    assert fsm.fire(DemoState.NEW, DemoEvent.FINISH) is DemoState.DONE
    assert fsm.fire(DemoState.NEW, DemoEvent.FINISH) is DemoState.DONE
    assert _sample("smo_fsm_transitions_total", **labels) == before + 2

    refused = dict(machine="DemoState", from_state="DONE", event="REOPEN")
    before = _sample("smo_fsm_illegal_transitions_total", **refused)
    with pytest.raises(IllegalTransition):
        fsm.fire(DemoState.DONE, DemoEvent.REOPEN)
    assert _sample("smo_fsm_illegal_transitions_total", **refused) == before + 1


def test_a_guard_that_rejects_counts_as_a_refusal_not_a_transition():
    import enum
    from smo_shared.statemachine import IllegalTransition, StateMachine

    class GuardedState(enum.Enum):
        A = "A"
        B = "B"

    fsm = StateMachine().add(GuardedState.A, "go", GuardedState.B, guard=lambda **ctx: ctx.get("ok", False))
    ok = dict(machine="GuardedState", from_state="A", event="go", to_state="B")
    no = dict(machine="GuardedState", from_state="A", event="go")
    t0, i0 = _sample("smo_fsm_transitions_total", **ok), _sample("smo_fsm_illegal_transitions_total", **no)
    with pytest.raises(IllegalTransition):
        fsm.fire(GuardedState.A, "go", ok=False)
    assert fsm.fire(GuardedState.A, "go", ok=True) is GuardedState.B
    assert _sample("smo_fsm_transitions_total", **ok) == t0 + 1 and _sample("smo_fsm_illegal_transitions_total", **no) == i0 + 1


# --- PR-OBS-2.4: database pool gauges ----------------------------------------------------------------------------------------------

def test_pool_gauges_follow_checkouts_and_returns(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.pool import QueuePool
    from smo_shared.metrics import PoolCollector

    engine = create_engine(f"sqlite:///{tmp_path / 'pool.db'}", poolclass=QueuePool, pool_size=2, max_overflow=1)
    collector = PoolCollector(lambda: engine)

    def gauges():
        out = {}
        for family in collector.collect():
            for sample in family.samples:
                out[(sample.name, tuple(sorted(sample.labels.items())))] = sample.value
        return out

    def state(name):
        return gauges()[("smo_db_pool_connections", (("state", name),))]

    assert state("in_use") == 0 and gauges()[("smo_db_pool_capacity", ())] == 3
    held = [engine.connect() for _ in range(3)]                       # two in the pool plus one overflow
    assert state("in_use") == 3 and state("overflow") == 1 and state("idle") == 0
    for conn in held:
        conn.close()
    assert state("in_use") == 0 and state("idle") == 2 and state("overflow") == 0


def test_a_pool_that_is_not_a_queue_pool_reports_nothing():
    from smo_shared.metrics import PoolCollector
    from smo_shared.testing import make_test_engine

    assert list(PoolCollector(make_test_engine).collect()) == []


def test_the_scrape_carries_the_pool_and_fsm_families_for_the_modules_own_engine():
    body = TestClient(_app()).get("/metrics").text
    assert "# TYPE smo_fsm_transitions_total counter" in body


def test_a_service_without_database_credentials_can_install_metrics():
    """R1 Termination and the mock services have no database: installing metrics must not import smo_shared.db (the first version did, and
    the compose stack failed to start with MissingDatabaseUrl)."""
    import os
    import subprocess
    import sys
    env = {k: v for k, v in os.environ.items() if not k.startswith("SMO_DATABASE") and k != "SMO_ALLOW_INSECURE_DEFAULT_DB"}
    env["PYTHONPATH"] = os.pathsep.join(sys.path)
    code = ("import sys\nfrom fastapi import FastAPI\nfrom smo_shared.metrics import install_metrics\n"
            "app = FastAPI(); install_metrics(app)\nassert 'smo_shared.db' not in sys.modules\nprint('ok')")
    done = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert done.returncode == 0 and "ok" in done.stdout, done.stderr
