"""Shared plumbing of the two code-documentation tools (`verify_docs_only.py`, `check_code_docs.py`).

What lives here: the git calls both make (read a file at a ref, list what changed between two refs), the runners for the two helpers that are not
Python (the Go programs in `docs_tools/` and the Node program `docs_tools/ts_tool.mjs`), and one lexer for the C-like languages (Java and SQL) that tells
code, comment and string literal apart, so neither tool is fooled by `"//"` inside a string or a quote inside a comment.

Where it sits: imported by the two scripts next to it (`sys.path` is the scripts directory when they run); `tests_integration/test_code_docs_tools.py` exercises
it through them. The rule the tools enforce is `docs/CODE_DOCUMENTATION.md`.

What it does not own: deciding what a file needs (that is `check_code_docs.py`) or whether a change is only a comment (that is `verify_docs_only.py`).
Standard library only; the helpers need `go` (Go files) and `node` plus `smo/gui/node_modules` (TypeScript files) and report clearly when one is missing.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
SMO = SCRIPTS.parent
TOOLS = SCRIPTS / "docs_tools"


class ToolError(Exception):
    """A helper the tools depend on (git, go, node) is missing or failed; the message says what to install or run."""


def git(args: list[str], cwd: Path, *, check: bool = True, text: bool = True) -> subprocess.CompletedProcess:
    """Runs `git <args>` in `cwd` and returns the finished process; with `check` a non-zero exit raises ToolError carrying git's own message."""
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=text, check=False)  # noqa: S603, S607 - fixed command, caller-supplied refs only as arguments
    if check and proc.returncode != 0:
        err = proc.stderr if text else proc.stderr.decode("utf-8", "replace")
        raise ToolError(f"git {' '.join(args)} failed: {err.strip()}")
    return proc


def repo_root(start: Path = SMO) -> Path:
    """The top directory of the git work tree that holds `start`."""
    return Path(git(["rev-parse", "--show-toplevel"], start).stdout.strip())


def read_at(root: Path, ref: str | None, path: str) -> str | None:
    """The text of repo-relative `path` at git `ref` (None means the working tree), or None when the file does not exist there."""
    if ref is None:
        p = root / path
        return p.read_text(encoding="utf-8", errors="replace") if p.is_file() else None
    proc = git(["show", f"{ref}:{path}"], root, check=False, text=False)
    return proc.stdout.decode("utf-8", "replace") if proc.returncode == 0 else None


@dataclass(frozen=True)
class Change:
    """One changed path: status A (added), D (deleted), M (modified) or R (renamed, `old` is the previous path)."""

    status: str
    path: str
    old: str | None = None


def changed_files(root: Path, base: str, head: str | None) -> list[Change]:
    """Files that differ between `base` and `head` (None: the working tree, uncommitted and untracked files included), renames detected."""
    args = ["diff", "--name-status", "-M", "-z", base] + ([head] if head else [])
    parts = git(args, root).stdout.split("\0")
    out: list[Change] = []
    i = 0
    while i < len(parts) and parts[i]:
        status = parts[i][0]
        if status in "RC":
            out.append(Change("R", parts[i + 2], parts[i + 1]))
            i += 3
        else:
            out.append(Change(status, parts[i + 1]))
            i += 2
    if head is None:
        # `git diff <base>` leaves out files git does not track yet: a new file of a documentation change is still a change.
        for p in git(["ls-files", "--others", "--exclude-standard", "-z"], root).stdout.split("\0"):
            if p:
                out.append(Change("A", p))
    return out


# --- the Go helpers -----------------------------------------------------------------------------------------------------------------------------

_go_bins: dict[str, Path] = {}
_go_tmp: tempfile.TemporaryDirectory | None = None


def go_tool(name: str) -> Path:
    """Compiles `docs_tools/<name>.go` once per process and returns the binary (`go run` cannot take a mix of directories and _test.go files as arguments)."""
    global _go_tmp
    if name in _go_bins:
        return _go_bins[name]
    if shutil.which("go") is None:
        raise ToolError("`go` is not installed: Go files cannot be examined")
    if _go_tmp is None:
        _go_tmp = tempfile.TemporaryDirectory(prefix="docs-tools-go-")
    out = Path(_go_tmp.name) / name
    proc = subprocess.run(["go", "build", "-o", str(out), str(TOOLS / f"{name}.go")], cwd=_go_tmp.name, capture_output=True, text=True, check=False)  # noqa: S603, S607
    if proc.returncode != 0:
        raise ToolError(f"cannot build docs_tools/{name}.go: {proc.stderr.strip()}")
    _go_bins[name] = out
    return out


def run_go(name: str, texts: dict[str, str], *, test_suffix: dict[str, bool] | None = None) -> dict[str, dict]:
    """Runs a Go helper over `texts` ({key: source}) and returns {key: its JSON object}. A key whose `test_suffix` is true is saved as `_test.go`."""
    binary = go_tool(name)
    with tempfile.TemporaryDirectory(prefix="docs-tools-src-") as d:
        names: dict[str, str] = {}
        for i, (key, text) in enumerate(texts.items()):
            suffix = "_test.go" if (test_suffix or {}).get(key) else ".go"
            p = Path(d) / f"f{i}{suffix}"
            p.write_text(text, encoding="utf-8")
            names[str(p)] = key
        if not names:
            return {}
        proc = subprocess.run([str(binary), *names], capture_output=True, text=True, check=False, cwd=d)  # noqa: S603
        if proc.returncode != 0:
            raise ToolError(f"{name} failed: {proc.stderr.strip()}")
        result = {}
        for line in proc.stdout.splitlines():
            obj = json.loads(line)
            result[names[obj["file"]]] = obj
        return result


# --- the Node helper ----------------------------------------------------------------------------------------------------------------------------

def run_ts(mode: str, jobs: list[dict]) -> dict[str, dict]:
    """Runs `docs_tools/ts_tool.mjs` (`canon` or `docs`) over jobs [{id, lang, text}] and returns {id: result}."""
    if not jobs:
        return {}
    if shutil.which("node") is None:
        raise ToolError("`node` is not installed: TypeScript files cannot be examined")
    proc = subprocess.run(["node", str(TOOLS / "ts_tool.mjs"), mode], input=json.dumps(jobs), capture_output=True, text=True, check=False)  # noqa: S603, S607
    if proc.returncode != 0:
        raise ToolError(proc.stderr.strip() or "ts_tool.mjs failed")
    return json.loads(proc.stdout)


# --- a lexer for Java and SQL -------------------------------------------------------------------------------------------------------------------

_DOLLAR = re.compile(r"\$[A-Za-z_]*\$")  # the opening tag of a PostgreSQL dollar-quoted string

def scan_c_like(text: str, *, sql: bool = False) -> list[tuple[str, int, int]]:
    """Splits `text` into ("code" | "comment" | "string", start, end) segments that cover it completely.

    Java (`sql=False`): `//` and `/* */` comments, `"..."` strings, `'.'` chars and `\"\"\"` text blocks, with backslash escapes. SQL (`sql=True`): `--` and nested
    `/* */` comments, `'...'` strings with `''` for a quote, `"..."` identifiers and `$tag$...$tag$` bodies. A literal or comment that is never closed runs to
    the end of the text (the caller's compile step is what reports it).
    """
    segs: list[tuple[str, int, int]] = []
    n = len(text)
    i = 0
    code_start = 0

    def flush(upto: int) -> None:
        if upto > code_start:
            segs.append(("code", code_start, upto))

    while i < n:
        c = text[i]
        two = text[i:i + 2]
        end = -1
        kind = ""
        if (two == "--" and sql) or (two == "//" and not sql):
            kind = "comment"
            end = text.find("\n", i)
            end = n if end < 0 else end
        elif two == "/*":
            kind = "comment"
            if sql:  # PostgreSQL block comments nest
                depth, j = 1, i + 2
                while j < n and depth:
                    if text[j:j + 2] == "/*":
                        depth, j = depth + 1, j + 2
                    elif text[j:j + 2] == "*/":
                        depth, j = depth - 1, j + 2
                    else:
                        j += 1
                end = j
            else:
                j = text.find("*/", i + 2)
                end = n if j < 0 else j + 2
        elif not sql and text.startswith('"""', i):
            kind = "string"
            j = i + 3
            while j < n and not text.startswith('"""', j):
                j += 2 if text[j] == "\\" else 1
            end = min(n, j + 3)
        elif c == '"' or (c == "'" and not sql) or (c == "'" and sql):
            kind = "string"
            j = i + 1
            while j < n:
                if sql:
                    if text[j] == c:
                        if text[j + 1:j + 2] == c:  # '' is an escaped quote
                            j += 2
                            continue
                        break
                    j += 1
                else:
                    if text[j] == "\\":
                        j += 2
                        continue
                    if text[j] == c or text[j] == "\n":
                        break
                    j += 1
            end = min(n, j + 1)
        elif sql and c == "$":
            m = _DOLLAR.match(text, i)
            if m:
                tag = m.group(0)
                j = text.find(tag, m.end())
                kind = "string"
                end = n if j < 0 else j + len(tag)
        if kind:
            flush(i)
            segs.append((kind, i, end))
            i = code_start = end
        else:
            i += 1
    flush(n)
    return segs


def blank_c_like(text: str, *, sql: bool = False, keep_strings: bool = False) -> str:
    """`text` with every comment (and, unless `keep_strings`, every string literal's contents) replaced by spaces; newlines and all offsets stay where they were."""
    out = []
    for kind, a, b in scan_c_like(text, sql=sql):
        seg = text[a:b]
        if kind == "code" or (kind == "string" and keep_strings):
            out.append(seg)
        elif kind == "comment":
            out.append("".join(ch if ch == "\n" else " " for ch in seg))
        else:  # string: keep the quotes so the shape of the code survives
            out.append(seg[0] + "".join(ch if ch == "\n" else " " for ch in seg[1:-1]) + seg[-1] if len(seg) >= 2 else seg)
    return "".join(out)


# --- Python helpers shared by both tools ---------------------------------------------------------------------------------------------------------

ROUTE_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "api_route", "websocket"}


def deco_name(d: ast.expr) -> str:
    """The dotted name of a decorator, with call arguments dropped (`app.get("/x")` becomes `app.get`)."""
    if isinstance(d, ast.Call):
        d = d.func
    parts = []
    while isinstance(d, ast.Attribute):
        parts.append(d.attr)
        d = d.value
    if isinstance(d, ast.Name):
        parts.append(d.id)
    return ".".join(reversed(parts))


def is_route(fn: ast.AST) -> bool:
    """A function decorated `@<anything>.get/post/put/patch/delete/...` (a FastAPI app or router)."""
    return any(deco_name(d).rsplit(".", 1)[-1] in ROUTE_METHODS and "." in deco_name(d) for d in getattr(fn, "decorator_list", []))


def published_docstrings(tree: ast.AST) -> dict[str, str]:
    """{name: docstring} of the route functions and pydantic models in a module: FastAPI publishes these texts in the OpenAPI document."""
    out: dict[str, str] = {}
    models: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.ClassDef):
            bases = {b.attr if isinstance(b, ast.Attribute) else getattr(b, "id", "") for b in n.bases}
            if bases & ({"BaseModel"} | models):
                models.add(n.name)
                out[n.name] = ast.get_docstring(n) or ""
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and is_route(n):
            out[n.name] = ast.get_docstring(n) or ""
    return out
