"""PR-ST-6: no outbound HTTP call in service code is made without an explicit timeout.

httpx's own default is an implicit 5 s: it hangs nothing, but it silently fails a healthy slow operation (it did
R1 Termination's proxy) and nobody chose it. Every `httpx.get/post/...` call and every `httpx.Client` /
`httpx.AsyncClient` in `<module>/app`, `shared`, `sdk` and `samples/*/app` must pass `timeout=`. A call on a client
that was built with a timeout needs none, so only the module-level functions and the constructors are checked.
The values themselves are in `smo_shared/timeouts.py`.
"""

import ast
import importlib.util
from pathlib import Path

import pytest

SMO_ROOT = Path(__file__).resolve().parents[1]
CALLS = {"get", "post", "put", "patch", "delete", "head", "options", "request", "stream"}
CLIENTS = {"Client", "AsyncClient"}


def _scan_files(root: Path = SMO_ROOT) -> list[Path]:
    spec = importlib.util.spec_from_file_location("check_statelessness", SMO_ROOT / "scripts" / "check_statelessness.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.scan_files(root)      # the same files the statelessness guard covers (no tests, no mocks)


def untimed_calls(source: str, name: str = "<test>") -> list[str]:
    """Returns a message `<name>:<line>: httpx.<call>(...) has no timeout=` for each module-level httpx request function or client constructor in
    the source that does not pass `timeout=`; calls on an already configured client are not checked.
    """
    found = []
    for node in ast.walk(ast.parse(source, filename=name)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "httpx"):
            continue
        if node.func.attr in CALLS | CLIENTS and not any(k.arg == "timeout" for k in node.keywords):
            found.append(f"{name}:{node.lineno}: httpx.{node.func.attr}(...) has no timeout=")
    return found


def test_every_outbound_http_call_in_service_code_has_an_explicit_timeout():
    """No service code (module apps, shared, the SDK and the sample apps) makes an httpx call without an explicit timeout, because httpx's implicit
    5 seconds was nobody's choice.
    """
    findings = []
    for path in _scan_files():
        findings += untimed_calls(path.read_text(), path.relative_to(SMO_ROOT).as_posix())
    assert findings == [], "outbound HTTP calls without a timeout (see smo_shared/timeouts.py):\n" + "\n".join(findings)


# One row per source snippet; each must give exactly one finding.
@pytest.mark.parametrize("source", [
    "import httpx\nhttpx.get('http://x')\n",
    "import httpx\nhttpx.post('http://x', json={})\n",
    "import httpx\nasync def f():\n    async with httpx.AsyncClient() as c:\n        pass\n",
    "import httpx\nc = httpx.Client(transport=None)\n",
])
def test_the_guard_flags_a_call_without_a_timeout(source):
    """The guard flags each way of making a call without a timeout: a bare get or post and a client of either kind with no timeout."""
    assert len(untimed_calls(source)) == 1


# One row per source snippet; none may give a finding.
@pytest.mark.parametrize("source", [
    "import httpx\nhttpx.get('http://x', timeout=5.0)\n",
    "import httpx\nc = httpx.AsyncClient(timeout=None)\n",       # an explicit decision, even if it is "none"
    "import httpx\nhttpx.post('http://x', timeout=timeout)\n",
    "import httpx\nclient.request('GET', 'http://x')\n",          # on a configured client: not checked
    "import httpx\nx = httpx.Response(200)\n",                    # not a request
])
def test_the_guard_accepts_an_explicit_timeout_and_ignores_other_calls(source):
    """The guard accepts any explicit timeout (even None, which is a decision) and ignores calls on a configured client and things that are not
    requests.
    """
    assert untimed_calls(source) == []
