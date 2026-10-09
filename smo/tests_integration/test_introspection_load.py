"""PR-SEC-5.4: the load runner counts the introspections SME answers, and the comparison of a run without the cache against one with it says what it shows.

The measurement itself (a real gateway and a real SME under load) is the CI lane `smo-load.yml` (and `HISTORY.md` PR-SEC-5.4b for a run without Docker); these tests pin the
arithmetic and the verdict, so that a counter that moves, or a comparison that passes a run in which the cache was not what it says, fails here first.
"""

import importlib.util
import json
from pathlib import Path

import pytest

SMO_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, SMO_ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


load_run = _load("load_run")
compare = _load("introspection_compare")

SME_PAGE = """\
# HELP smo_http_requests_total HTTP requests handled
# TYPE smo_http_requests_total counter
smo_http_requests_total{method="POST",route="/oauth2/introspect",status="200"} 120.0
smo_http_requests_total{method="POST",route="/oauth2/introspect",status="401"} 5.0
smo_http_requests_total{method="POST",route="/oauth2/token",status="200"} 7.0
smo_http_requests_total_created{method="POST",route="/oauth2/introspect",status="200"} 1.7e9
"""
GATEWAY_PAGE = """\
smo_http_requests_total{method="GET",route="/{service}/{path:path}",status="200"} 300.0
smo_http_requests_total{method="GET",route="/metrics",status="200"} 9.0
smo_http_requests_total{method="GET",route="/live",status="200"} 40.0
smo_introspection_cache_total{result="hit"} 250.0
smo_introspection_cache_total{result="miss"} 50.0
"""


def test_metric_sum_adds_the_matching_series_and_only_those():
    assert load_run.metric_sum(SME_PAGE, "smo_http_requests_total", route="/oauth2/introspect") == 125.0
    assert load_run.metric_sum(SME_PAGE, "smo_http_requests_total", route="/oauth2/introspect", status="401") == 5.0
    assert load_run.metric_sum(SME_PAGE, "smo_http_requests_total", route="/nothing") == 0.0, "no such series is zero, not an error"
    assert load_run.metric_sum(GATEWAY_PAGE, "smo_http_requests_total", not_routes=load_run.NOT_PROXIED) == 300.0, "the gateway's own routes are not token checks"
    assert load_run.metric_sum(GATEWAY_PAGE, "smo_introspection_cache_total", result="hit") == 250.0


def _counters(introspect, requests, hit=0, miss=0):
    return {"sme_introspect": introspect, "gateway_requests": requests, "cache_hit": hit, "cache_miss": miss}


def test_the_summary_counts_calls_by_the_gateway_and_falls_back_to_the_ones_the_runner_saw():
    off = load_run.introspection_summary(_counters(1000, 5000), _counters(1500, 5500), gateway_calls=480)
    assert off == {"sme_introspections": 500, "gateway_calls": 500, "introspections_per_100_calls": 100.0, "cache_hits": 0, "cache_misses": 0, "cache_hit_ratio": None}
    on = load_run.introspection_summary(_counters(1500, 5500, 0, 0), _counters(1503, 6500, 990, 10), gateway_calls=1000)
    assert on["introspections_per_100_calls"] == 0.3 and on["cache_hit_ratio"] == 0.99
    fallback = load_run.introspection_summary(_counters(0, 0), _counters(40, 0), gateway_calls=50)
    assert fallback["gateway_calls"] == 50 and fallback["introspections_per_100_calls"] == 80.0
    assert load_run.introspection_summary(None, _counters(1, 1), 10) is None, "a page that could not be read leaves the part out"


def _run(per_100, hits=0, misses=0, errors=0, calls=1000, with_intro=True):
    intro = {"sme_introspections": int(calls * per_100 / 100), "gateway_calls": calls, "introspections_per_100_calls": per_100, "cache_hits": hits, "cache_misses": misses,
             "cache_hit_ratio": round(hits / (hits + misses), 3) if hits + misses else None}
    return {"total": {"route": "all", "requests": calls, "rps": 50.0, "p50": 20.0, "p95": 80.0, "p99": 120.0, "max": 200.0, "errors": errors}, "introspection": intro if with_intro else None}


def test_a_cache_that_cuts_the_introspections_passes():
    problems, findings = compare.verdict(_run(99.0), _run(1.0, hits=990, misses=10), 0.5)
    assert problems == [] and "99.0% fewer" in findings[0]


@pytest.mark.parametrize("off, on, message", [
    (_run(99.0), _run(60.0, hits=400, misses=600), "cut the introspections by 39%"),
    (_run(99.0), _run(99.0), "counted no cache lookups"),
    (_run(40.0), _run(1.0, hits=990, misses=10), "only 40.0 introspections per 100 calls"),
    (_run(99.0, hits=10, misses=5), _run(1.0, hits=990, misses=10), "the cache was on in it"),
    (_run(99.0, errors=3), _run(1.0, hits=990, misses=10), "had 3 errors"),
    (_run(99.0), _run(1.0, hits=990, misses=10, errors=1), "with the cache had 1 errors"),
    (_run(99.0, with_intro=False), _run(1.0, hits=990, misses=10), "no introspection counters"),
])
def test_a_comparison_that_does_not_show_what_it_says_fails(off, on, message):
    problems, _ = compare.verdict(off, on, 0.5)
    assert any(message in p for p in problems), problems


def test_main_reads_the_two_directories_writes_the_table_and_exits_by_the_verdict(tmp_path, capsys):
    for name, run in (("off", _run(99.0)), ("on", _run(1.0, hits=990, misses=10))):
        (tmp_path / name).mkdir()
        (tmp_path / name / "load-results.json").write_text(json.dumps(run))
    out = tmp_path / "table.md"
    assert compare.main(["--off", str(tmp_path / "off"), "--on", str(tmp_path / "on"), "--seconds", "30", "--out", str(out)]) == 0
    text = out.read_text()
    assert "| cache off |" in text and "| cache 30 s |" in text and "99.0% hits" in text
    (tmp_path / "on" / "load-results.json").write_text(json.dumps(_run(99.0, hits=1, misses=1)))
    assert compare.main(["--off", str(tmp_path / "off"), "--on", str(tmp_path / "on")]) == 1
    assert "PROBLEM" in capsys.readouterr().out
