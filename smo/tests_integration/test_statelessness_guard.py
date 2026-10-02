"""scripts/check_statelessness.py: service code keeps no request-visible state.

`test_services_hold_no_unlisted_process_state` is the guard itself, run on the
real tree. The other tests prove the guard fires, so it cannot rot into a
check that passes on anything.
"""

import importlib.util
from pathlib import Path

import pytest

SMO_ROOT = Path(__file__).resolve().parents[1]


def _guard():
    spec = importlib.util.spec_from_file_location("check_statelessness", SMO_ROOT / "scripts" / "check_statelessness.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _guard()


def _tree(tmp_path: Path, source: str, module: str = "demo") -> Path:
    app = tmp_path / module / "app"
    app.mkdir(parents=True)
    (app / "main.py").write_text(source)
    return tmp_path


def _rules(tmp_path: Path, source: str) -> list[str]:
    new, _ = guard.check(_tree(tmp_path, source), allowlist={})
    return sorted(line.rsplit("[", 1)[1].split(":")[1] for line in new)


def test_services_hold_no_unlisted_process_state():
    new, stale = guard.check()
    assert new == [], "new in-process state in service code:\n" + "\n".join(new)
    assert stale == [], "stale allowlist entries:\n" + "\n".join(stale)


@pytest.mark.parametrize("source,rule", [
    ("_seen = {}\n", "module-state"),
    ("_seen: list[str] = []\n", "module-state"),
    ("from collections import deque\n_q = deque()\n", "module-state"),
    ("import threading\n_lock = threading.Lock()\n", "module-state"),
    ("_cache = {k: 1 for k in range(3)}\n", "module-state"),
    ("def f():\n    global counter\n    counter = 1\n", "global-statement"),
    ("import functools\n@functools.lru_cache\ndef f():\n    return 1\n", "cache"),
    ("from functools import cache\n@cache\ndef f():\n    return 1\n", "cache"),
    ("import threading\ndef f():\n    threading.Thread(target=f).start()\n", "background-work"),
    ("import asyncio\nasync def f():\n    asyncio.create_task(f())\n", "background-work"),
    ("from fastapi import BackgroundTasks\ndef f(b: BackgroundTasks):\n    pass\n", "background-work"),
    ("import apscheduler\n", "background-work"),
    ("class Holder:\n    def __init__(self):\n        self.x = 1\nholder = Holder()\n", "instance-state"),
    ("app = object()\napp.state.seen = {}\n", "app-state"),
])
def test_guard_flags(tmp_path, source, rule):
    assert rule in _rules(tmp_path, source)


@pytest.mark.parametrize("source", [
    "ROUTES = {'a': 1}\n",                       # ALL_CAPS is a constant
    "_FSM = build()\n",                          # a function call, not a container
    "NAMES = ('a', 'b')\n_names = ('a',)\n",     # immutable
    "class Pure:\n    pass\nx = Pure()\n",       # no __init__ state
    "def f():\n    seen = {}\n    return seen\n", # local, not module state
    "import asyncio\nasync def f():\n    await asyncio.gather()\n",  # within-request concurrency
])
def test_guard_ignores(tmp_path, source):
    assert _rules(tmp_path, source) == []


def test_mocks_and_tests_are_not_scanned(tmp_path):
    _tree(tmp_path, "_policies = {}\n", module="mock-near-rt-ric")
    tests = tmp_path / "demo" / "app" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_x.py").write_text("_x = {}\n")
    (tmp_path / "demo" / "app" / "ok.py").write_text("X = 1\n")
    assert guard.check(tmp_path, allowlist={}) == ([], [])


def test_allowlisted_finding_passes_and_a_stale_entry_fails(tmp_path):
    root = _tree(tmp_path, "_seen = {}\n")
    key = "demo/app/main.py:module-state:_seen"
    assert guard.check(root, allowlist={key: "ok"}) == ([], [])
    new, stale = guard.check(root, allowlist={key: "ok", "demo/app/gone.py:cache:f": "old"})
    assert new == [] and len(stale) == 1


def test_allowlist_entries_need_a_reason(tmp_path):
    bad = tmp_path / "list.txt"
    bad.write_text("a/app/x.py:cache:f\n")
    with pytest.raises(SystemExit):
        guard.load_allowlist(bad)
