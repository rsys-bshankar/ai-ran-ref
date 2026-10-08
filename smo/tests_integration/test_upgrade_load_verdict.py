"""The verdict of the load through an upgrade (scripts/upgrade_load_verdict.py, PR-V-10)."""

import importlib.util
import json
from pathlib import Path

SMO_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("upgrade_load_verdict", SMO_ROOT / "scripts" / "upgrade_load_verdict.py")
v = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(v)


def result(calls=5000, errors=0, routes=None, timeline=None):
    return {"total": {"requests": calls, "errors": errors},
            "routes": routes if routes is not None else [{"route": "alarm list", "errors": 0, "statuses": {200: 100}}, {"route": "package list", "errors": 0, "statuses": {200: 100}}],
            "timeline": timeline or [], "seconds": 600}


def test_a_load_that_ran_and_was_answered_passes():
    assert v.verdict(result()) == []


def test_a_load_that_made_too_few_calls_did_not_run_through_the_upgrade():
    assert "only 1999 calls were made (at least 2000 expected)" in v.verdict(result(calls=1999))[0]
    assert v.verdict(result(calls=2000)) == []


def test_the_error_rate_is_judged_over_the_whole_run():
    assert v.verdict(result(calls=5000, errors=50)) == []                        # 1 %: the budget
    assert "51 of 5000 calls were errors (1.02% > 1.00%)" in v.verdict(result(calls=5000, errors=51))[0]
    assert v.verdict(result(calls=5000, errors=51), max_error_rate=0.02) == []


def test_a_route_that_is_not_allowed_a_gap_must_have_no_errors_and_one_that_is_may():
    routes = [{"route": "alarm list", "errors": 1, "statuses": {200: 99, 503: 1}}, {"route": "package list", "errors": 40, "statuses": {200: 60, 0: 40}}]
    problems = v.verdict(result(errors=41, routes=routes), max_error_rate=0.05)
    assert len(problems) == 1 and problems[0].startswith("alarm list: 1 errors")
    assert v.verdict(result(errors=1, routes=[{"route": "alarm list", "errors": 1, "statuses": {}}]), max_route_errors=1) == []


def test_the_command_prints_when_the_errors_were_and_exits_1_on_failure(tmp_path, capsys):
    path = tmp_path / "r.json"
    path.write_text(json.dumps(result(errors=0, timeline=[{"from_s": 0, "to_s": 10, "calls": 100, "errors": 0}, {"from_s": 120, "to_s": 130, "calls": 100, "errors": 4}],
                                      routes=[{"route": "alarm list", "errors": 4, "statuses": {0: 4}}])))
    assert v.main([str(path)]) == 1
    out = capsys.readouterr()
    assert "120-130 s: 4 errors of 100 calls" in out.out and "0-10 s" not in out.out and "FAIL: alarm list: 4 errors" in out.err
    path.write_text(json.dumps(result()))
    assert v.main([str(path)]) == 0


def test_report_only_prints_the_problems_and_exits_zero(tmp_path, capsys):
    path = tmp_path / "r.json"
    path.write_text(json.dumps(result(calls=300, errors=100)))
    assert v.main([str(path)]) == 1
    assert v.main([str(path), "--report-only"]) == 0
    assert "NOTE (report only)" in capsys.readouterr().err


def test_the_failed_calls_are_printed_for_the_log(tmp_path, capsys):
    result = {"total": {"requests": 3000, "errors": 1}, "routes": [], "timeline": [{"from_s": 140, "to_s": 150, "calls": 18, "errors": 1}],
              "failures": [{"at": "02:44:41.123", "t_s": 140.2, "route": "alarm list", "status": 503, "ms": 812, "detail": "AUTH_SERVICE_UNAVAILABLE"}]}
    path = tmp_path / "r.json"
    path.write_text(json.dumps(result))
    assert v.main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "02:44:41.123" in out and "alarm list" in out and "503" in out and "AUTH_SERVICE_UNAVAILABLE" in out
