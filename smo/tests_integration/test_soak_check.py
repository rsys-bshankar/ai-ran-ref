"""scripts/soak_check.py (PR-V-9b): a metric that grows past both the relative and the absolute floor fails; a flat or a small one does not."""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("soak_check", Path(__file__).parent.parent / "scripts" / "soak_check.py")
soak = importlib.util.module_from_spec(spec)
spec.loader.exec_module(soak)


def series(values):
    return [(i * 60, float(v)) for i, v in enumerate(values)]


def test_a_flat_metric_passes():
    rows, ok = soak.judge({"mem:r1-termination": series([200] * 30)}, 0.25)
    assert ok and rows[0]["verdict"] == "flat"


def test_a_leak_fails():
    rows, ok = soak.judge({"mem:r1-termination": series(range(100, 400, 10))}, 0.25)
    assert not ok and rows[0]["verdict"] == "GROWING"


def test_growth_below_the_floor_is_not_a_leak():
    rows, ok = soak.judge({"pg_connections": series([2] * 15 + [5] * 15)}, 0.25)    # 2 to 5 is +150 %, but three connections
    assert ok


def test_too_few_samples_fail_rather_than_pass_silently():
    rows, ok = soak.judge({"outbox_pending": series([0, 0, 0])}, 0.25)
    assert not ok and rows[0]["verdict"] == "too few samples"


def test_warm_up_is_ignored():
    rows, ok = soak.judge({"mem:sme": series([50] * 3 + [300] * 27)}, 0.25)           # the first tenth is warm-up; the rest is flat
    assert ok
