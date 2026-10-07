"""scripts/load_run.py: the pacing, the stop file and the timeline of errors that the upgrade-under-load lane (PR-V-10) reads."""

import asyncio
import importlib.util
import time
from pathlib import Path
from types import SimpleNamespace

SMO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("load_run", SMO_ROOT / "scripts" / "load_run.py")
load_run = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(load_run)


def run_worker(monkeypatch, statuses, seconds=0.3, interval=0.0, stop_file=None):
    calls = iter(statuses)

    async def one_call(client, gateway, sme, reg, access, route):
        try:
            return next(calls)
        except StopIteration:
            return 200

    monkeypatch.setattr(load_run, "one_call", one_call)
    series = {route[0]: load_run.Series() for route in load_run.ROUTES}
    timeline: dict[int, list[int]] = {}
    import random
    now = time.monotonic()
    asyncio.run(load_run.worker(None, "g", "s", {}, "t", now + seconds, now, series, [r[3] for r in load_run.ROUTES], random.Random(1), timeline, interval, stop_file))
    return series, timeline


def test_a_call_is_an_error_when_its_status_is_not_the_one_the_route_expects_and_the_timeline_counts_both(monkeypatch):
    series, timeline = run_worker(monkeypatch, [200, 503, 0, 200])
    assert sum(s.errors for s in series.values()) >= 2
    calls = sum(cell[0] for cell in timeline.values())
    assert calls == sum(len(s.latencies_ms) for s in series.values()) and sum(cell[1] for cell in timeline.values()) == sum(s.errors for s in series.values())


def test_the_pacing_keeps_each_call_at_least_one_interval_apart(monkeypatch):
    series, _ = run_worker(monkeypatch, [], seconds=0.5, interval=0.1)
    assert 3 <= sum(len(s.latencies_ms) for s in series.values()) <= 6          # about 5 in half a second, not the thousands an unpaced loop makes


def test_a_stop_file_ends_the_worker_before_its_deadline(monkeypatch, tmp_path):
    stop = tmp_path / "stop"
    stop.write_text("")
    started = time.monotonic()
    series, _ = run_worker(monkeypatch, [], seconds=30, stop_file=str(stop))
    assert time.monotonic() - started < 2 and sum(len(s.latencies_ms) for s in series.values()) == 0
    assert load_run.stopped(str(stop)) and not load_run.stopped(str(tmp_path / "no")) and not load_run.stopped(None)


def test_the_timeline_rows_name_each_ten_second_slice():
    rows = load_run.timeline_rows({2: [50, 3], 0: [100, 0]})
    assert rows == [{"from_s": 0, "to_s": 10, "calls": 100, "errors": 0}, {"from_s": 20, "to_s": 30, "calls": 50, "errors": 3}]


def test_the_markdown_says_when_the_errors_were_or_that_there_were_none():
    base = {"routes": [], "total": {"route": "all", "requests": 150, "rps": 15.0, "p50": 1, "p95": 2, "p99": 3, "max": 4, "errors": 3}, "seconds": 30.0}
    args = SimpleNamespace(concurrency=4, duration=60, warmup=5, rate=20)
    bad = load_run.markdown({**base, "timeline": load_run.timeline_rows({2: [50, 3]})}, args)
    assert "Errors by time since the end of the warm-up: 20-30 s: 3 of 50" in bad and "for 30 s after a 5 s warm-up, paced to about 20 calls a second" in bad
    assert "No errors in any 10 s of the run." in load_run.markdown({**base, "timeline": load_run.timeline_rows({0: [10, 0]})}, SimpleNamespace(concurrency=4, duration=60, warmup=5, rate=0))
