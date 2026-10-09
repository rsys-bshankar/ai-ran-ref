"""scripts/obs_smoke.py (PR-OBS-3.6, PR-OBS-6.4): the pure logic, and the whole run against a fake HTTP layer. No docker.

What only CI can show is the live stack (the CI job `obs-stack`): that Tempo, Loki, Fluent Bit and Grafana accept the files in
`deploy/helm/smo/files/observability/` and that the answers have the shape these functions expect.
"""

import importlib.util
import json
import re
from pathlib import Path

import yaml

SMO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("obs_smoke", SMO_ROOT / "scripts" / "obs_smoke.py")
obs = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(obs)

TID = "4bf92f3577b34da6a3ce929d0e0e4736"


def test_trace_id_and_traceparent_are_valid_w3c():
    tid = obs.new_trace_id()
    assert re.fullmatch(r"[0-9a-f]{32}", tid) and int(tid, 16)
    header = obs.make_traceparent(tid)
    assert re.fullmatch(rf"00-{tid}-[0-9a-f]{{16}}-01", header)
    assert int(header.split("-")[2], 16)


def test_count_spans_reads_v1_and_v2_envelopes_and_ignores_the_rest():
    v1 = {"batches": [{"scopeSpans": [{"spans": [{"name": "a"}, {"name": "b"}]}]}, {"scopeSpans": [{"spans": [{"name": "c"}]}]}]}
    v2 = {"trace": {"resourceSpans": [{"scopeSpans": [{"spans": [{"name": "a"}]}]}]}}
    assert obs.count_spans(v1) == 3 and obs.count_spans(v2) == 1
    assert obs.count_spans({"batches": []}) == 0 and obs.count_spans(None) == 0 and obs.count_spans("spans") == 0
    assert obs.count_spans({"spans": "not a list"}) == 0


def test_ready_needs_200_and_the_word_ready():
    assert obs.is_ready(200, "ready\n") and obs.is_ready(200, "Ready")
    assert not obs.is_ready(503, "Ingester not ready: waiting for 15s after being ready")
    assert not obs.is_ready(200, "starting") and not obs.is_ready(0, "URLError")


def test_loki_query_is_the_documented_one():
    assert obs.loki_query(TID) == '{job="smo"} | json | traceId="' + TID + '"'


def _loki_answer(*lines, result_type="streams"):
    return {"status": "success", "data": {"resultType": result_type,
                                          "result": [{"stream": {"job": "smo", "service": "r1-termination", "level": "INFO"},
                                                      "values": [["1700000000000000000", line] for line in lines]}]}}


def test_find_request_line_requires_the_line_itself_to_carry_the_trace_id():
    mine = json.dumps({"logger": "smo.access", "traceId": TID, "status": 200})
    other = json.dumps({"logger": "smo.access", "traceId": "0" * 31 + "1"})
    assert obs.find_request_line(_loki_answer(other, "not json", mine), TID)["status"] == 200
    assert obs.find_request_line(_loki_answer(other, "not json"), TID) is None
    # a line that merely mentions the id in its text is not a match
    assert obs.find_request_line(_loki_answer(json.dumps({"message": TID})), TID) is None
    assert obs.find_request_line(_loki_answer(mine, result_type="matrix"), TID) is None
    for broken in (None, {}, {"data": None}, {"data": {"resultType": "streams", "result": [{"values": [["1"]]}]}}, []):
        assert obs.find_request_line(broken, TID) is None


def test_tempo_echo():
    assert obs.tempo_echo_ok(200, "echo\n") and not obs.tempo_echo_ok(200, "") and not obs.tempo_echo_ok(502, "echo")


def test_grafana_checks():
    assert obs.datasource_ok(200, {"status": "OK", "message": "Data source is working"})
    assert not obs.datasource_ok(200, {"status": "ERROR"}) and not obs.datasource_ok(500, {"status": "OK"})
    assert not obs.datasource_ok(200, None) and not obs.datasource_ok(404, {"message": "not found"})
    assert obs.grafana_healthy(200, {"database": "ok", "version": "13"}) and not obs.grafana_healthy(200, {"database": "failing"})
    assert not obs.grafana_healthy(0, None)


def test_retry_until_stops_at_success_and_at_the_deadline():
    now = [0.0]
    slept = []

    def sleep(seconds):
        slept.append(seconds)
        now[0] += seconds

    answers = iter(["not yet", "not yet", None])
    assert obs.retry_until(lambda: next(answers), 60, 2, clock=lambda: now[0], sleep=sleep) is None and slept == [2, 2]

    now[0], slept[:] = 0.0, []
    assert obs.retry_until(lambda: "never", 10, 3, clock=lambda: now[0], sleep=sleep) == "never"
    assert sum(slept) >= 10 and len(slept) <= 5
    # a zero deadline still tries once
    calls = []
    assert obs.retry_until(lambda: calls.append(1) or "no", 0, 1, clock=lambda: now[0], sleep=sleep) == "no" and calls == [1]


class FakeStack:
    """Answers like a healthy stack, with switches to break one part."""

    def __init__(self, spans=True, log=True, tempo_source="OK", gateway=200):
        self.spans, self.log, self.tempo_source, self.gateway = spans, log, tempo_source, gateway
        self.trace_id = None
        self.seen = []

    def http(self, method, url, headers=None, timeout=10.0):
        self.seen.append(url)
        if url.endswith("/ready"):
            return 200, "ready\n"
        if url.endswith("/bootstrap"):
            self.trace_id = headers["traceparent"].split("-")[1]
            return self.gateway, "{}"
        if url.endswith("/api/datasources/proxy/uid/tempo/api/echo"):
            return (200, "echo") if self.tempo_source == "OK" else (502, "bad gateway")
        code, body = self.http_json(url)
        return code, json.dumps(body)

    def http_json(self, url):
        self.seen.append(url)
        if "/api/traces/" in url or "/api/v2/traces/" in url:
            assert url.rsplit("/", 1)[1] == self.trace_id
            if self.spans:
                return 200, {"batches": [{"scopeSpans": [{"spans": [{"name": "GET /bootstrap"}]}]}]}
            return 404, None
        if "/loki/api/v1/query_range" in url:
            assert self.trace_id in url
            lines = [json.dumps({"logger": "smo.access", "traceId": self.trace_id, "status": 200})] if self.log else []
            return 200, _loki_answer(*lines)
        if url.endswith("/api/health"):
            return 200, {"database": "ok"}
        if url.endswith("/api/datasources/uid/tempo/health"):
            return 404, {"message": "plugin.notImplemented"}    # Grafana's Tempo data source has no backend health check
        if url.endswith("/api/datasources/uid/loki/health"):
            return 200, {"status": "OK"}
        return 404, None


def run(monkeypatch, capsys, stack):
    monkeypatch.setattr(obs, "http", stack.http)
    monkeypatch.setattr(obs, "http_json", stack.http_json)
    monkeypatch.setattr(obs.time, "sleep", lambda s: None)
    code = obs.main(["--deadline", "0", "--interval", "0"])
    return code, capsys.readouterr().out


def test_a_healthy_stack_passes_and_the_output_holds_no_header_or_body(monkeypatch, capsys):
    code, out = run(monkeypatch, capsys, FakeStack())
    assert code == 0 and "all observability checks passed" in out and "FAIL" not in out
    assert "traceparent" not in out.lower() and "database" not in out


def test_each_missing_part_fails_the_run(monkeypatch, capsys):
    for kwargs, name in (({"spans": False}, "trace is found"), ({"log": False}, "log line"), ({"tempo_source": "ERROR"}, "reaches Tempo")):
        code, out = run(monkeypatch, capsys, FakeStack(**kwargs))
        assert code == 1 and "FAIL" in out and name in out, (kwargs, out)


def test_a_gateway_that_does_not_answer_fails_before_the_lookups(monkeypatch, capsys):
    stack = FakeStack(gateway=401)
    code, out = run(monkeypatch, capsys, stack)
    assert code == 1 and "answered 401" in out
    assert not [u for u in stack.seen if "/api/traces/" in u]


def test_the_ci_job_runs_this_script_against_both_profiles_and_dumps_the_three_services_on_failure():
    workflow = yaml.safe_load((SMO_ROOT.parent / ".github" / "workflows" / "smo-tests.yml").read_text())
    job = workflow["jobs"]["obs-stack"]
    text = yaml.safe_dump(job)
    assert "scripts/obs_smoke.py" in text and "--profile tracing" in text and "--profile logging" in text
    assert "SMO_WITH_TRACING" in text and "SMO_OTEL_ENDPOINT" in text
    failure = [s for s in job["steps"] if s.get("if") == "failure()"]
    assert failure and all(name in failure[0]["run"] for name in ("tempo", "loki", "fluent-bit", "grafana"))
    for step in job["steps"]:
        if "uses" in step:
            assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", step["uses"]), step["uses"]


def test_the_compose_services_the_script_talks_to_exist_with_the_ports_it_assumes():
    compose = yaml.safe_load((SMO_ROOT / "docker-compose.yml").read_text())["services"]
    assert compose["tempo"]["profiles"] == ["tracing"] and set(compose["loki"]["profiles"]) == {"logging"}
    assert set(compose["grafana"]["profiles"]) == {"tracing", "logging"}
    files = SMO_ROOT / "deploy" / "helm" / "smo" / "files" / "observability"
    assert yaml.safe_load((files / "tempo.yaml").read_text())["server"]["http_listen_port"] == 3200
    assert yaml.safe_load((files / "loki.yaml").read_text())["server"]["http_listen_port"] == 3100
    uids = {d["uid"] for d in yaml.safe_load((files / "grafana-datasources.yaml").read_text())["datasources"]}
    assert uids == {"tempo", "loki"}
