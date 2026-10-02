#!/usr/bin/env python3
"""Guard: no request-visible state or background work in service code.

Every SMO module is meant to be runnable as N identical replicas
(docs/ARCHITECTURE.md, "Process state and scale-out"). That only holds while
service code keeps its state in Postgres, so this check fails the build when a
change adds in-process state or background work to a service, unless the
finding is listed in `statelessness_allowlist.txt` with a reason.

What it flags (AST, no imports, stdlib only):

  module-state        a module-level name bound to a mutable container
                      (`{}`, `[]`, `set()`, `deque()`, `Counter()`, ...) or a
                      lock / event / thread-local. ALL_CAPS names are treated as
                      constants and skipped.
  instance-state      a module-level `name = SomeClass()` where `SomeClass` is
                      defined in the same file and sets attributes in `__init__`.
  app-state           `app.state.<x> = <mutable>` (state hung on the FastAPI app).
  global-statement    `global x` inside a function.
  cache               `functools.lru_cache` / `functools.cache`.
  background-work     `threading.Thread/Timer`, `asyncio.create_task` /
                      `ensure_future`, `BackgroundTasks`, `ThreadPoolExecutor`,
                      `ProcessPoolExecutor`, `run_in_executor`, `sched`,
                      `apscheduler`.

Scope: `<module>/app/**` for every service, `shared/smo_shared`, `sdk/smo_sdk`
and `samples/*/app`. Not scanned: tests, and `mock-*` (test doubles, which hold
state on purpose).

Known limits: a constant written in ALL_CAPS is trusted to be a constant, and a
class instance is only recognised when its class is in the same file.

Usage:  python scripts/check_statelessness.py          (from smo/)
Exit 0 when clean, 1 on a new violation or a stale allowlist entry.
"""

import ast
import sys
from pathlib import Path

SMO_ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST = Path(__file__).resolve().with_name("statelessness_allowlist.txt")

MUTABLE_CALLS = {"dict", "list", "set", "bytearray", "deque", "defaultdict", "OrderedDict", "Counter",
                 "Lock", "RLock", "Semaphore", "BoundedSemaphore", "Event", "Condition", "local"}
CACHE_DECORATORS = {"lru_cache", "cache"}
BACKGROUND_CALLS = {"Thread", "Timer", "create_task", "ensure_future", "run_in_executor",
                    "ThreadPoolExecutor", "ProcessPoolExecutor", "BackgroundTasks"}
BACKGROUND_IMPORTS = {"sched", "apscheduler", "celery"}
MUTABLE_NODES = (ast.Dict, ast.List, ast.Set, ast.DictComp, ast.ListComp, ast.SetComp)


def scan_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for base in sorted(root.iterdir()):
        if base.name.startswith("mock-") or not base.is_dir():
            continue
        if (base / "app").is_dir():
            files += (base / "app").rglob("*.py")
    files += (root / "shared" / "smo_shared").rglob("*.py")
    files += (root / "sdk" / "smo_sdk").rglob("*.py")
    files += (root / "samples").glob("*/app/**/*.py")
    return sorted(f for f in set(files) if "tests" not in f.parts and "__pycache__" not in f.parts)


def _call_name(node: ast.AST) -> str:
    func = node.func if isinstance(node, ast.Call) else node
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _is_mutable(value: ast.AST) -> bool:
    return isinstance(value, MUTABLE_NODES) or (isinstance(value, ast.Call) and _call_name(value) in MUTABLE_CALLS)


def _is_app_state(target: ast.AST) -> str | None:
    """`app.state.x` (or `self.app.state.x`) -> "x"."""
    if isinstance(target, ast.Attribute) and isinstance(target.value, ast.Attribute) and target.value.attr == "state":
        return target.attr
    return None


def _stateful_classes(tree: ast.Module) -> set[str]:
    names = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == "__init__":
                    if any(isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id == "self"
                           and isinstance(n.ctx, ast.Store) for n in ast.walk(item)):
                        names.add(node.name)
    return names


def _targets(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Assign):
        return [t.id for t in node.targets if isinstance(t, ast.Name)]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
        return [node.target.id]
    return []


def violations_in(path: Path, rel: str) -> list[tuple[str, str, int]]:
    """(key, message, line) for every finding in one file; key is `rel:rule:name`."""
    tree = ast.parse(path.read_text(), filename=rel)
    found: list[tuple[str, str, int]] = []

    def add(rule: str, name: str, line: int, msg: str) -> None:
        found.append((f"{rel}:{rule}:{name}", msg, line))

    stateful = _stateful_classes(tree)
    for node in tree.body:
        value = getattr(node, "value", None)
        for name in _targets(node):
            if name.isupper() or (name.startswith("__") and name.endswith("__")) or value is None:
                continue
            if _is_mutable(value):
                add("module-state", name, node.lineno, f"module-level mutable `{name}`")
            elif isinstance(value, ast.Call) and _call_name(value) in stateful:
                add("instance-state", name, node.lineno, f"module-level instance `{name}` of a stateful class")

    for node in ast.walk(tree):
        if isinstance(node, ast.Global):
            for name in node.names:
                add("global-statement", name, node.lineno, f"`global {name}`")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for deco in node.decorator_list:
                if _call_name(deco) in CACHE_DECORATORS:
                    add("cache", node.name, node.lineno, f"`@{_call_name(deco)}` on `{node.name}`")
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                attr = _is_app_state(target)
                if attr and node.value is not None and _is_mutable(node.value):
                    add("app-state", attr, node.lineno, f"`app.state.{attr}` holds a mutable container")
        elif isinstance(node, ast.Call):
            name = _call_name(node)
            if name in BACKGROUND_CALLS:
                add("background-work", name, node.lineno, f"`{name}(...)`")
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for module in modules:
                if module.split(".")[0] in BACKGROUND_IMPORTS:
                    add("background-work", module.split(".")[0], node.lineno, f"import of `{module}`")
            if isinstance(node, ast.ImportFrom) and any(a.name == "BackgroundTasks" for a in node.names):
                add("background-work", "BackgroundTasks", node.lineno, "import of `BackgroundTasks`")
    return found


def load_allowlist(path: Path = ALLOWLIST) -> dict[str, str]:
    entries: dict[str, str] = {}
    for n, raw in enumerate(path.read_text().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, reason = line.partition("#")
        if not sep or not reason.strip():
            raise SystemExit(f"{path.name}:{n}: every entry needs a `# reason`")
        entries[key.strip()] = reason.strip()
    return entries


def check(root: Path = SMO_ROOT, allowlist: dict[str, str] | None = None) -> tuple[list[str], list[str]]:
    """(new violations, stale allowlist entries), as printable lines."""
    allowed = load_allowlist() if allowlist is None else allowlist
    seen: set[str] = set()
    new: list[str] = []
    for path in scan_files(root):
        rel = path.relative_to(root).as_posix()
        for key, msg, line in violations_in(path, rel):
            seen.add(key)
            if key not in allowed:
                new.append(f"{rel}:{line}: {msg}  [{key}]")
    stale = [f"{key}  (no longer found: delete this allowlist entry)" for key in allowed if key not in seen]
    return new, stale


def main() -> int:
    new, stale = check()
    for line in new:
        print(f"NEW      {line}")
    for line in stale:
        print(f"STALE    {line}")
    if new:
        print("\nService code must not hold request-visible state or start background work "
              "(docs/ARCHITECTURE.md, 'Process state and scale-out').\nKeep the state in Postgres, or, if the "
              "finding is safe, add its key to scripts/statelessness_allowlist.txt with a reason.")
    if not new and not stale:
        print(f"statelessness: {len(scan_files(SMO_ROOT))} files scanned, no new in-process state")
    return 1 if (new or stale) else 0


if __name__ == "__main__":
    sys.exit(main())
