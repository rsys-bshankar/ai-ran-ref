"""The verdict of the shared-limiter scenario of scripts/stress_run.py (PR-V-9): what number of calls let through says about the store."""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("stress_run", SCRIPTS / "stress_run.py")
stress = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(stress)

ARGS = {"rate_burst": 20, "rate_per_second": 5.0, "slack": 8}


# One row per number of calls let through.
@pytest.mark.parametrize("allowed", [10, 20, 29, 38])
def test_one_budget_is_what_a_shared_store_lets_through(allowed):
    """With a shared limiter store about one budget (the burst plus the refill over the burst, plus a tolerance) is let through; any count from 10
    to the upper bound passes.
    """
    assert stress.shared_verdict(allowed, 2.0, shared=True, **ARGS) is None            # 20 + 5 x 2 + 8 = 38


def test_three_budgets_are_a_shared_store_that_is_not_shared():
    """Three budgets let through means the replicas each keep their own bucket, so the store is not shared and the verdict says so."""
    assert "keep their own buckets" in stress.shared_verdict(60, 2.0, shared=True, **ARGS)


def test_a_shared_store_that_lets_almost_nothing_through_is_refused_too():
    """A store that lets far fewer calls through than one budget is refused too, the verdict naming it as far below one budget."""
    assert "far below" in stress.shared_verdict(3, 2.0, shared=True, **ARGS)


def test_the_control_must_show_the_replicas_keeping_their_own_budgets():
    """The control run with per-replica stores must show about three budgets, and a run that cannot tell the two apart is reported."""
    assert stress.shared_verdict(60, 2.0, shared=False, **ARGS) is None
    assert "cannot tell the two apart" in stress.shared_verdict(25, 2.0, shared=False, **ARGS)


def test_a_longer_burst_has_more_refilled():
    """A longer burst allows more calls through because the bucket refills meanwhile, so the same count passes for 5 seconds and not for 1."""
    assert stress.shared_verdict(45, 5.0, shared=True, **ARGS) is None                  # 20 + 25 + 8 = 53
    assert stress.shared_verdict(45, 1.0, shared=True, **ARGS) is not None              # 20 + 5 + 8 = 33
